"""Retrieve publication abstracts via a multi-source waterfall.

Tries each source in order and returns the first abstract found:

    1. OpenAlex  — best coverage, longest abstracts
    2. Crossref  — fallback for DOIs not yet in OpenAlex
    3. PubMed    — catches Nature/Elsevier holdouts (~86% biomedical coverage)
    4. Content negotiation — universal last resort

Before hitting any API, the DOI is validated and classified. If the DOI is
invalid or not a publication (e.g., dataset, award), the function refuses
gracefully and returns an error message instead of wasting API calls.

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
from pydantic import BaseModel

from nmdc_metadata_suggestor.publication_ingestion.doi_utils import (
    DEFAULT_TIMEOUT,
    USER_AGENT,
    classify_doi,
    normalize_doi,
)

# API endpoints
OPENALEX_API = "https://api.openalex.org/works"
CROSSREF_API = "https://api.crossref.org/works"
PUBMED_ID_CONVERTER = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


class AbstractResult(BaseModel):
    """Result of an abstract retrieval attempt.

    Attributes:
        doi: The normalized DOI that was looked up.
        abstract: The abstract as clean plain text (tags stripped, entities
            unescaped, inverted index reconstructed), or None if not found.
        raw_abstract: The abstract exactly as the API returned it, before any
            parsing or stripping. For OpenAlex this is the JSON-serialized
            inverted index; for Crossref/content negotiation it may contain
            JATS XML tags; for PubMed it equals ``abstract`` (already plain
            text). Use this when you need the original markup or structure.
        source: Which source provided the abstract
            (``"openalex"``, ``"crossref"``, ``"pubmed"``, ``"content_negotiation"``).
        content_format: The raw format of the response before parsing:

            - ``"inverted_index"`` — OpenAlex ``{word: [positions...]}``
              (reconstructed; may lose punctuation spacing)
            - ``"jats_xml"`` — JATS XML tags stripped (Crossref, content negotiation)
            - ``"plain_text"`` — already clean text (PubMed efetch, or Crossref/
              content negotiation when no XML tags were present)
            - ``"citeproc_json"`` — Citeproc JSON ``abstract`` field (content
              negotiation, when no JATS tags detected)
            - ``None`` — no abstract was found

        pmid: PubMed ID, populated only when PubMed was the source.
        attempts: Sources tried in order, for debugging.
        error: Human-readable error if the DOI was refused or all sources failed.
    """

    doi: str
    abstract: str | None = None
    raw_abstract: str | None = None
    source: str | None = None
    content_format: str | None = None
    pmid: str | None = None
    attempts: list[str] = []
    error: str | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_abstract(doi: str, skip_classification: bool = False) -> AbstractResult:
    """Try each source in waterfall order, return the first abstract found.

    Before fetching, validates the DOI and checks that it is a publication.
    Dataset, award, and DMP DOIs are refused with a clear error message.
    Pass ``skip_classification=True`` to bypass this check (e.g., if you
    already know the DOI is a publication).

    Args:
        doi: A DOI string in any common format (bare, URL, ``doi:`` prefix).
        skip_classification: If True, skip DOI validation/classification and
            go straight to the waterfall. Useful when the caller has already
            verified the DOI type.

    Returns:
        AbstractResult with the abstract text and metadata, or an error.
    """
    doi = normalize_doi(doi)

    if not skip_classification:
        classification = classify_doi(doi)
        if not classification.is_valid:
            return AbstractResult(
                doi=doi,
                error=f"Invalid DOI: {classification.error or 'validation failed'}",
            )
        category = classification.inferred_nmdc_category
        if category and category != "publication_doi":
            return AbstractResult(
                doi=doi,
                error=(
                    f"DOI is a {category}, not a publication. "
                    "Abstract retrieval is only supported for publication DOIs."
                ),
            )

    attempts: list[str] = []

    # 1. OpenAlex
    attempts.append("openalex")
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

    # 2. Crossref
    attempts.append("crossref")
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

    # 3. PubMed
    attempts.append("pubmed")
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

    # 4. Content negotiation
    attempts.append("content_negotiation")
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

    return AbstractResult(doi=doi, attempts=attempts, error="No abstract found in any source")


# ---------------------------------------------------------------------------
# Per-source fetchers
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
    except requests.RequestException:
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
    except requests.RequestException:
        return None, None, None


def _try_pubmed(doi: str) -> tuple[str | None, str | None]:
    """Fetch abstract from PubMed via DOI → PMID → efetch.

    Returns:
        (abstract_text, pmid) tuple. Both are None if PubMed has no record.
    """
    # Step 1: DOI → PMID via ID converter
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
    except requests.RequestException:
        return None, None

    # Step 2: PMID → abstract via efetch
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
    except requests.RequestException:
        return None, None, None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def decode_inverted_abstract(inverted_index: dict[str, list[int]]) -> str:
    """Reconstruct plain text from an OpenAlex inverted abstract index.

    OpenAlex stores abstracts as ``{word: [position_0, position_1, ...]}``.
    We rebuild the original text by placing each word at its position(s).

    Args:
        inverted_index: Mapping of word → list of integer positions.

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
