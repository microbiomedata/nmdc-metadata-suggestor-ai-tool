"""Retrieve publication abstracts via a multi-source waterfall.

See ``docs/doi-classification-design.md`` §7 for design rationale,
waterfall order, and format classification details.

Programmatic usage::

    from nmdc_metadata_suggestor.publication_ingestion.abstract_retriever import (
        get_abstract,
    )

    result = get_abstract("10.1038/s41564-020-00861-0")
    if result.abstract:
        print(result.abstract)
    else:
        print(f"No abstract found (tried: {result.attempts})")

CLI usage::

    make get-abstract DOI=10.1038/s41564-020-00861-0
"""

import json

import requests

from nmdc_metadata_suggestor.constants import (
    ALL_SOURCES,
    DEFAULT_TIMEOUT,
    OPENALEX_API_URL,
    PUBMED_EFETCH,
    PUBMED_ID_CONVERTER,
    USER_AGENT,
)
from nmdc_metadata_suggestor.doi_ingestion.content_negotiation import (
    try_content_negotiation_abstract,
)
from nmdc_metadata_suggestor.doi_ingestion.crossref import try_crossref_abstract
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    classify_doi,
    normalize_doi,
    request_with_retry,
)
from nmdc_metadata_suggestor.models.doi import DoiClassification, SourceRetrievalResult

# DataCite resourceTypeGeneral values that are clearly not publications.
# These are unmapped in doi_utils (inferred_nmdc_category returns None),
# but we can still refuse them in the abstract retrieval gate.
# Source: DataCite Metadata Schema 4.6
DATACITE_NON_PUBLICATION_TYPES = {
    "Software",
    "Workflow",
    "ComputationalNotebook",
    "Collection",
    "Image",
    "Audiovisual",
    "Sound",
    "Model",
    "Service",
    "Event",
    "InteractiveResource",
    "Instrument",
    "PhysicalObject",
}

# Crossref work types that are clearly not publications.
# "component" is a figure/table/supplement within a larger work.
CROSSREF_NON_PUBLICATION_TYPES = {
    "component",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_abstract(
    doi: str,
    skip_classification: bool = False,
    sources: list[str] | None = None,
) -> SourceRetrievalResult:
    """Try each source in waterfall order, return the first abstract found.

    Before fetching, classifies the DOI and checks that it is a publication.
    Dataset, award, and DMP DOIs are refused with a clear error message.
    Pass ``skip_classification=True`` to bypass this check (e.g., if you
    already know the DOI is a publication).

    Args:
        doi: A DOI string in any common format (bare, URL, ``doi:`` prefix).
        skip_classification: If True, skip DOI classification and go straight
            to the waterfall. Useful when the caller has already verified the
            DOI type.
        sources: Which sources to try, in order. Defaults to all five:
            ``["openalex", "crossref", "pubmed", "content_negotiation", "osti"]``.
            Pass a subset to skip sources or change the order, e.g.
            ``sources=["crossref", "pubmed"]`` to skip OpenAlex.

    Returns:
        SourceRetrievalResult with the abstract text and metadata, or an error.
    """
    # TODO : Is it better practice if we know a certain DOI prefix will go to a
    # certain source to just call that source directly instead of going through the whole waterfall?
    # For example, if we know an OSTI DOI will only be found via the OSTI source,
    # should we just call that function directly instead of going through the whole waterfall
    # and checking each source in order? Does this work the same with openalx and crossref?
    doi = normalize_doi(doi)

    if sources is None:
        sources = list(ALL_SOURCES)

    if not skip_classification:
        classification = classify_doi(doi)
        refusal = _check_classification_gate(classification)
        if refusal:
            return SourceRetrievalResult(doi=doi, error=refusal)

    attempts: list[str] = []

    for source in sources:
        if source not in _SOURCE_FETCHERS:
            continue
        attempts.append(source)
        result = _SOURCE_FETCHERS[source](doi, attempts)
        if result is not None:
            return result

    return SourceRetrievalResult(
        doi=doi,
        attempts=attempts,
        error="No abstract found in any source",
    )


# ---------------------------------------------------------------------------
# Classification gate
# ---------------------------------------------------------------------------


def _check_classification_gate(c: DoiClassification) -> str | None:
    """Check all classification axes and return a refusal reason, or None to proceed.

    Checks in order:
    1. Is the DOI valid?
    2. NMDC category — refuse dataset_doi, award_doi, data_management_plan_doi
    3. DataCite resourceTypeGeneral — refuse Software, Collection, Image, etc.
    4. Crossref resource type — refuse component (figure/table within a work)

    Returns:
        Human-readable refusal string, or None if the DOI should proceed.
    """
    if not c.is_valid:
        return f"Invalid DOI: {c.error or 'validation failed'}"

    # Check NMDC category (most specific signal)
    if c.inferred_nmdc_category and c.inferred_nmdc_category != "publication_doi":
        return (
            f"DOI is a {c.inferred_nmdc_category}, not a publication. "
            "Abstract retrieval is only supported for publication DOIs."
        )

    # Check DataCite resourceTypeGeneral (catches unmapped non-publications)
    if c.resource_type_general and c.resource_type_general in DATACITE_NON_PUBLICATION_TYPES:
        return (
            f"DOI has DataCite resourceTypeGeneral={c.resource_type_general!r}, "
            "which is not a publication type."
        )

    # Check Crossref type (catches unmapped non-publications)
    if c.resource_type and c.resource_type in CROSSREF_NON_PUBLICATION_TYPES:
        return f"DOI has Crossref type={c.resource_type!r}, which is not a publication type."

    return None


# ---------------------------------------------------------------------------
# Source dispatch — maps source names to waterfall step functions.
# Each returns SourceRetrievalResult on success, None to try the next source.
# ---------------------------------------------------------------------------


def _fetch_osti(doi: str, attempts: list[str]) -> SourceRetrievalResult | None:
    """
    Fetch abstract from OSTI (for OSTI DOIs only).

    Args:
        doi: A DOI string in any common format (bare, URL, ``doi:`` prefix).
        attempts: List of sources attempted so far

    Returns:
        SourceRetrievalResult containing the abstract/description from OSTI, or an error.
    """
    if not doi.startswith("10.15485/"):  # OSTI prefix
        return None

    try:
        from nmdc_metadata_suggestor.publication_ingestion.osti_publication_retriever import (
            retrieve_doi_info_from_osti,
        )

        pub = retrieve_doi_info_from_osti(doi)
        if pub.abstract:
            return SourceRetrievalResult(
                doi=doi,
                abstract=pub.abstract,
                raw_abstract=pub.abstract,
                source="osti",
                content_format="plain_text",
                attempts=attempts,
            )
    except Exception:
        pass
    return None


def _fetch_openalex(doi: str, attempts: list[str]) -> SourceRetrievalResult | None:
    text, raw, fmt = _try_openalex(doi)
    if text:
        return SourceRetrievalResult(
            doi=doi,
            abstract=text,
            raw_abstract=raw,
            source="openalex",
            content_format=fmt,
            attempts=attempts,
        )
    return None


def _fetch_crossref(doi: str, attempts: list[str]) -> SourceRetrievalResult | None:
    text, raw, fmt = try_crossref_abstract(doi)
    if text:
        return SourceRetrievalResult(
            doi=doi,
            abstract=text,
            raw_abstract=raw,
            source="crossref",
            content_format=fmt,
            attempts=attempts,
        )
    return None


def _fetch_pubmed(doi: str, attempts: list[str]) -> SourceRetrievalResult | None:
    text, pmid = _try_pubmed(doi)
    if text:
        return SourceRetrievalResult(
            doi=doi,
            abstract=text,
            raw_abstract=text,
            source="pubmed",
            content_format="plain_text",
            pmid=pmid,
            attempts=attempts,
        )
    return None


def _fetch_content_negotiation(doi: str, attempts: list[str]) -> SourceRetrievalResult | None:
    text, raw, fmt = try_content_negotiation_abstract(doi)
    if text:
        return SourceRetrievalResult(
            doi=doi,
            abstract=text,
            raw_abstract=raw,
            source="content_negotiation",
            content_format=fmt,
            attempts=attempts,
        )
    return None


_SOURCE_FETCHERS = {
    "openalex": _fetch_openalex,
    "crossref": _fetch_crossref,
    "pubmed": _fetch_pubmed,
    "content_negotiation": _fetch_content_negotiation,
    "osti": _fetch_osti,
}


# ---------------------------------------------------------------------------
# Per-source fetchers (low-level HTTP + parsing)
# ---------------------------------------------------------------------------


def _try_openalex(doi: str) -> tuple[str | None, str | None, str | None]:
    """Fetch abstract from OpenAlex.

    OpenAlex stores abstracts as an inverted index: ``{word: [positions...]}``.
    We reconstruct plain text by sorting on position.

    Returns:
        (cleaned_text, raw_text, content_format) tuple.
    """
    try:
        response = request_with_retry(
            "GET",
            f"{OPENALEX_API_URL}/https://doi.org/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None, None, None
        data = response.json()
        inverted = data.get("abstract_inverted_index")
        if inverted:
            raw = json.dumps(inverted, ensure_ascii=False)
            return decode_inverted_abstract(inverted), raw, "inverted_index"
        return None, None, None
    except (requests.RequestException, ValueError):
        return None, None, None


def _try_pubmed(doi: str) -> tuple[str | None, str | None]:
    """Fetch abstract from PubMed via DOI -> PMID -> efetch.

    Returns:
        (abstract_text, pmid) tuple. Both are None if PubMed has no record.
    """
    # Step 1: DOI -> PMID via ID converter
    try:
        response = request_with_retry(
            "GET",
            PUBMED_ID_CONVERTER,
            params={"ids": doi, "format": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None, None
        data = response.json()
        records = data.get("records", [])
        if not records:
            return None, None
        pmid = records[0].get("pmid")
        if not pmid or pmid == "0":
            return None, None
    except (requests.RequestException, ValueError):
        return None, None

    # Step 2: PMID -> abstract via efetch
    try:
        response = request_with_retry(
            "GET",
            PUBMED_EFETCH,
            params={"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "text"},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None, pmid
        text = response.text.strip()
        if text:
            return text, pmid
        return None, pmid
    except requests.RequestException:
        return None, pmid


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def decode_inverted_abstract(inverted_index: dict[str, list[int]]) -> str:
    """Reconstruct plain text from an OpenAlex inverted abstract index.

    OpenAlex stores abstracts as ``{word: [position_0, position_1, ...]}``,
    e.g. ``{"While": [0, 140], "in": [6, 181, 191]}``.  This format is
    optimized for search indexing — you can find which abstracts contain a
    word without reconstructing the full text, and it compresses well since
    common words aren't stored repeatedly.

    We rebuild the original text by placing each word at its position(s) and
    joining with spaces.  This is technically lossy: punctuation attached to
    words (``"soil,"`` at position 82) survives, but any non-space whitespace
    or formatting in the original is lost.  That's why ``SourceRetrievalResult``
    exposes both ``abstract`` (reconstructed) and ``raw_abstract`` (the
    original JSON-serialized inverted index).

    Args:
        inverted_index: Mapping of word -> list of integer positions.

    Returns:
        Reconstructed abstract as a single string.
    """
    words: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word
    if not words:
        return ""
    max_pos = max(words)
    return " ".join(words.get(i, "") for i in range(max_pos + 1))
