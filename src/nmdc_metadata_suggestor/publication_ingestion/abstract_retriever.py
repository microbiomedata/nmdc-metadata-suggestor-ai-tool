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

import html
import json
import re
import xml.etree.ElementTree as ET

import requests

from nmdc_metadata_suggestor.models.doi import AbstractResult, DoiClassification
from nmdc_metadata_suggestor.publication_ingestion.doi_utils import (
    DEFAULT_TIMEOUT,
    USER_AGENT,
    classify_doi,
    normalize_doi,
)

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

# API endpoints
OPENALEX_API = "https://api.openalex.org/works"
CROSSREF_API = "https://api.crossref.org/works"
PUBMED_ID_CONVERTER = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


ALL_SOURCES = ("openalex", "crossref", "pubmed", "content_negotiation")


def get_abstract(
    doi: str,
    skip_classification: bool = False,
    sources: list[str] | None = None,
) -> AbstractResult:
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
        sources: Which sources to try, in order. Defaults to all four:
            ``["openalex", "crossref", "pubmed", "content_negotiation"]``.
            Pass a subset to skip sources or change the order, e.g.
            ``sources=["crossref", "pubmed"]`` to skip OpenAlex.

    Returns:
        AbstractResult with the abstract text and metadata, or an error.
    """
    doi = normalize_doi(doi)

    if sources is None:
        sources = list(ALL_SOURCES)

    if not skip_classification:
        classification = classify_doi(doi)
        refusal = _check_classification_gate(classification)
        if refusal:
            return AbstractResult(doi=doi, error=refusal)

    attempts: list[str] = []

    for source in sources:
        if source not in _SOURCE_FETCHERS:
            continue
        attempts.append(source)
        result = _SOURCE_FETCHERS[source](doi, attempts)
        if result is not None:
            return result

    return AbstractResult(doi=doi, attempts=attempts, error="No abstract found in any source")


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
# Each returns AbstractResult on success, None to try the next source.
# ---------------------------------------------------------------------------


def _fetch_openalex(doi: str, attempts: list[str]) -> AbstractResult | None:
    text, raw, fmt = _try_openalex(doi)
    if text:
        return AbstractResult(
            doi=doi,
            abstract=text,
            raw_abstract=raw,
            source="openalex",
            content_format=fmt,
            attempts=attempts,
        )
    return None


def _fetch_crossref(doi: str, attempts: list[str]) -> AbstractResult | None:
    text, raw, fmt = _try_crossref(doi)
    if text:
        return AbstractResult(
            doi=doi,
            abstract=text,
            raw_abstract=raw,
            source="crossref",
            content_format=fmt,
            attempts=attempts,
        )
    return None


def _fetch_pubmed(doi: str, attempts: list[str]) -> AbstractResult | None:
    text, pmid = _try_pubmed(doi)
    if text:
        return AbstractResult(
            doi=doi,
            abstract=text,
            raw_abstract=text,
            source="pubmed",
            content_format="plain_text",
            pmid=pmid,
            attempts=attempts,
        )
    return None


def _fetch_content_negotiation(doi: str, attempts: list[str]) -> AbstractResult | None:
    text, raw, fmt = _try_content_negotiation(doi)
    if text:
        return AbstractResult(
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
        response = requests.get(
            f"{OPENALEX_API}/https://doi.org/{doi}",
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


def _try_crossref(doi: str) -> tuple[str | None, str | None, str | None]:
    """Fetch abstract from Crossref.

    Crossref returns abstracts as JATS XML (``<jats:p>...</jats:p>``).

    Returns:
        (cleaned_text, raw_text, content_format) tuple.
    """
    try:
        response = requests.get(
            f"{CROSSREF_API}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None, None, None
        msg = response.json().get("message", {})
        raw = msg.get("abstract")
        if raw:
            fmt = "jats_xml" if "<" in raw else "plain_text"
            return strip_jats_xml(raw), raw, fmt
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
        response = requests.get(
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
        response = requests.get(
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


def _try_content_negotiation(doi: str) -> tuple[str | None, str | None, str | None]:
    """Fetch abstract via DOI content negotiation (Citeproc JSON).

    This works for any registration agency as a universal last resort.
    The ``abstract`` field, if present, may contain JATS XML.

    Returns:
        (cleaned_text, raw_text, content_format) tuple.
    """
    try:
        response = requests.get(
            f"https://doi.org/{doi}",
            headers={
                "Accept": "application/vnd.citationstyles.csl+json",
                "User-Agent": USER_AGENT,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None, None, None
        data = response.json()
        raw = data.get("abstract")
        if raw:
            fmt = "jats_xml" if "<" in raw else "citeproc_json"
            return strip_jats_xml(raw), raw, fmt
        return None, None, None
    except (requests.RequestException, ValueError):
        return None, None, None


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
    or formatting in the original is lost.  That's why ``AbstractResult``
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


def strip_jats_xml(xml_abstract: str) -> str:
    """Strip JATS XML tags from an abstract and unescape HTML entities.

    Crossref and content negotiation return abstracts wrapped in JATS tags
    like ``<jats:p>``, ``<jats:italic>``, etc. This strips all XML/HTML tags
    and unescapes entities like ``&amp;`` and ``&#x2019;``.

    Args:
        xml_abstract: Raw abstract text, possibly containing JATS XML.

    Returns:
        Clean plain-text abstract.
    """
    # Try XML parsing first (handles namespaces properly)
    try:
        root = ET.fromstring(f"<root>{xml_abstract}</root>")
        text = ET.tostring(root, encoding="unicode", method="text")
        return html.unescape(text).strip()
    except ET.ParseError:
        pass
    # Fallback: regex tag stripping
    text = re.sub(r"<[^>]+>", "", xml_abstract)
    return html.unescape(text).strip()
