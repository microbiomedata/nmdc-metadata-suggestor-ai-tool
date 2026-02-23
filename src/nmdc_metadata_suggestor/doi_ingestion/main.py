"""DOI description/abstract ingestion for repository-backed DOI providers.

This module retrieves text context for LLM prompting from DOI records, with a
waterfall that prioritizes provider APIs and then falls back to generic DOI
metadata APIs.
"""

import html
import re

import requests

from nmdc_metadata_suggestor.models.doi import DoiContextResult
from nmdc_metadata_suggestor.publication_ingestion.abstract_retriever import strip_jats_xml
from nmdc_metadata_suggestor.publication_ingestion.doi_utils import (
    CROSSREF_API,
    DATACITE_API,
    DEFAULT_TIMEOUT,
    DOI_PATTERN,
    USER_AGENT,
    normalize_doi,
)

DOI_CONTENT_NEGOTIATION_API = "https://doi.org"
ESS_DIVE_API = "https://api.ess-dive.lbl.gov/packages"
FIGSHARE_API = "https://api.figshare.com/v2/articles"
ZENODO_API = "https://zenodo.org/api/records"

TARGET_PROVIDER_PREFIXES: dict[str, str] = {
    "10.6073": "edi",
    "10.46936": "emsl",
    "10.15485": "ess-dive",
    "10.21952": "ess-dive",
    "10.6084": "figshare",
    "10.25585": "jgi",
    "10.25982": "kbase",
    "10.25345": "massive",
    "10.5281": "zenodo",
}

TARGET_PROVIDER_KEYWORDS: dict[str, str] = {
    "environmental data initiative": "edi",
    "emsl": "emsl",
    "ess-dive": "ess-dive",
    "figshare": "figshare",
    "genomic standards consortium": "gsc",
    "jgi": "jgi",
    "kbase": "kbase",
    "massive": "massive",
    "zenodo": "zenodo",
    "cyverse": "cyverse",
}

ALL_SOURCES = (
    "ess-dive",
    "figshare",
    "zenodo",
    "datacite",
    "crossref",
    "content_negotiation",
)


def get_doi_description_or_abstract(
    doi: str, sources: list[str] | None = None
) -> DoiContextResult:
    """Fetch abstract/description text for a DOI using a source waterfall.

    Args:
        doi: DOI in any common format.
        sources: Optional explicit source order. Supported values are in
            ``ALL_SOURCES``.

    Returns:
        DoiContextResult with the first abstract/description found.
    """
    doi = normalize_doi(doi)
    if not DOI_PATTERN.match(doi):
        return DoiContextResult(doi=doi, error="Invalid DOI: Malformed DOI syntax")

    provider = _infer_provider_from_doi(doi)
    if sources is None:
        sources = _default_source_order(provider)

    attempts: list[str] = []
    for source in sources:
        fetcher = _SOURCE_FETCHERS.get(source)
        if fetcher is None:
            continue
        attempts.append(source)
        result = fetcher(doi, provider, attempts)
        if result is not None:
            return result

    return DoiContextResult(
        doi=doi,
        provider=provider,
        attempts=attempts,
        error="No description or abstract found in any source",
    )


def _default_source_order(provider: str | None) -> list[str]:
    """Return the default source order for a provider-aware lookup."""
    if provider in _PROVIDER_API_SOURCES:
        return [provider, "datacite", "crossref", "content_negotiation"]
    return ["datacite", "crossref", "content_negotiation"]


def _infer_provider_from_doi(doi: str) -> str | None:
    """Infer a provider key from DOI prefix mapping."""
    prefix_match = re.match(r"^(10\.\d{4,9})/", doi)
    if not prefix_match:
        return None
    prefix = prefix_match.group(1)
    return TARGET_PROVIDER_PREFIXES.get(prefix)


def _infer_provider_from_text(text: str | None) -> str | None:
    """Infer a provider key from publisher/source text keywords."""
    if not text:
        return None
    lowered = text.lower()
    for key, provider in TARGET_PROVIDER_KEYWORDS.items():
        if key in lowered:
            return provider
    return None


def _build_result(
    doi: str,
    provider: str | None,
    source: str,
    attempts: list[str],
    context: str,
    raw_context: str,
    context_type: str,
) -> DoiContextResult:
    """Construct a normalized context result object."""
    return DoiContextResult(
        doi=doi,
        context=context,
        raw_context=raw_context,
        context_type=context_type,
        provider=provider,
        source=source,
        attempts=attempts,
    )


def _fetch_ess_dive(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context from ESS-DIVE-specific API."""
    context = _try_ess_dive(doi)
    if context is None:
        return None
    text, kind = context
    return _build_result(doi, provider, "ess-dive", attempts, text, text, kind)


def _fetch_figshare(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context from Figshare-specific API."""
    text = _try_figshare(doi)
    if text is None:
        return None
    return _build_result(doi, provider, "figshare", attempts, text, text, "description")


def _fetch_zenodo(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context from Zenodo-specific API."""
    text = _try_zenodo(doi)
    if text is None:
        return None
    return _build_result(doi, provider, "zenodo", attempts, text, text, "description")


def _fetch_datacite(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context from DataCite metadata."""
    context = _try_datacite(doi)
    if context is None:
        return None

    text, raw, kind, publisher = context
    inferred_provider = provider or _infer_provider_from_text(publisher)
    return _build_result(doi, inferred_provider, "datacite", attempts, text, raw, kind)


def _fetch_crossref(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context from Crossref metadata."""
    context = _try_crossref(doi)
    if context is None:
        return None

    text, raw, kind, publisher = context
    inferred_provider = provider or _infer_provider_from_text(publisher)
    return _build_result(doi, inferred_provider, "crossref", attempts, text, raw, kind)


def _fetch_content_negotiation(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context via DOI content negotiation."""
    context = _try_content_negotiation(doi)
    if context is None:
        return None
    text, raw, kind = context
    return _build_result(doi, provider, "content_negotiation", attempts, text, raw, kind)


_SOURCE_FETCHERS = {
    "ess-dive": _fetch_ess_dive,
    "figshare": _fetch_figshare,
    "zenodo": _fetch_zenodo,
    "datacite": _fetch_datacite,
    "crossref": _fetch_crossref,
    "content_negotiation": _fetch_content_negotiation,
}

_PROVIDER_API_SOURCES = {"ess-dive", "figshare", "zenodo"}


def _try_ess_dive(doi: str) -> tuple[str, str] | None:
    """Return (text, kind) from ESS-DIVE if available."""
    try:
        response = requests.get(
            ESS_DIVE_API,
            params={"doi": doi},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    extracted = _find_first_text(payload, keys=("abstract", "description"))
    if extracted is None:
        return None
    key, text = extracted
    cleaned = _clean_text(text)
    if not cleaned:
        return None
    return cleaned, "abstract" if key == "abstract" else "description"


def _try_figshare(doi: str) -> str | None:
    """Return Figshare description text for a DOI if present."""
    try:
        response = requests.get(
            FIGSHARE_API,
            params={"doi": doi},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    if isinstance(payload, list) and payload:
        candidate = payload[0]
        if isinstance(candidate, dict):
            description = candidate.get("description")
            if isinstance(description, str):
                cleaned = _clean_text(description)
                return cleaned or None
    if isinstance(payload, dict):
        description = payload.get("description")
        if isinstance(description, str):
            cleaned = _clean_text(description)
            return cleaned or None
    return None


def _try_zenodo(doi: str) -> str | None:
    """Return Zenodo description text for a DOI if present."""
    try:
        response = requests.get(
            ZENODO_API,
            params={"q": f'doi:"{doi}"'},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    hits = payload.get("hits", {}).get("hits", [])
    if not isinstance(hits, list):
        return None

    for hit in hits:
        if not isinstance(hit, dict):
            continue
        metadata = hit.get("metadata", {})
        if not isinstance(metadata, dict):
            continue
        description = metadata.get("description")
        if isinstance(description, str):
            cleaned = _clean_text(description)
            if cleaned:
                return cleaned
    return None


def _try_datacite(doi: str) -> tuple[str, str, str, str | None] | None:
    """Return cleaned/raw text from DataCite descriptions, preferring abstract."""
    try:
        response = requests.get(
            f"{DATACITE_API}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        attrs = response.json().get("data", {}).get("attributes", {})
        descriptions = attrs.get("descriptions")
        publisher = attrs.get("publisher")
        if not isinstance(descriptions, list):
            return None
    except (requests.RequestException, ValueError):
        return None

    preferred = _pick_datacite_description(descriptions)
    if preferred is None:
        return None
    raw, kind = preferred
    cleaned = _clean_text(raw)
    if not cleaned:
        return None
    return cleaned, raw, kind, publisher if isinstance(publisher, str) else None


def _pick_datacite_description(descriptions: list[object]) -> tuple[str, str] | None:
    """Pick preferred DataCite description entry: abstract, then first fallback."""
    abstract_candidate: str | None = None
    description_candidate: str | None = None

    for item in descriptions:
        if not isinstance(item, dict):
            continue
        text = item.get("description")
        if not isinstance(text, str) or not text.strip():
            continue
        description_type = item.get("descriptionType")
        if isinstance(description_type, str) and description_type.lower() == "abstract":
            if abstract_candidate is None:
                abstract_candidate = text
        elif description_candidate is None:
            description_candidate = text

    if abstract_candidate is not None:
        return abstract_candidate, "abstract"
    if description_candidate is not None:
        return description_candidate, "description"
    return None


def _try_crossref(doi: str) -> tuple[str, str, str, str | None] | None:
    """Return Crossref abstract text and publisher metadata if available."""
    try:
        response = requests.get(
            f"{CROSSREF_API}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        message = response.json().get("message", {})
    except (requests.RequestException, ValueError):
        return None

    raw = message.get("abstract")
    publisher = message.get("publisher")
    if not isinstance(raw, str) or not raw.strip():
        return None
    cleaned = strip_jats_xml(raw)
    if not cleaned:
        return None
    return cleaned, raw, "abstract", publisher if isinstance(publisher, str) else None


def _try_content_negotiation(doi: str) -> tuple[str, str, str] | None:
    """Return context from CSL JSON content negotiation (abstract then note)."""
    try:
        response = requests.get(
            f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
            headers={
                "Accept": "application/vnd.citationstyles.csl+json",
                "User-Agent": USER_AGENT,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    abstract = payload.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        cleaned = strip_jats_xml(abstract)
        if cleaned:
            return cleaned, abstract, "abstract"

    note = payload.get("note")
    if isinstance(note, str) and note.strip():
        cleaned = _clean_text(note)
        if cleaned:
            return cleaned, note, "description"
    return None


def _find_first_text(payload: object, keys: tuple[str, ...]) -> tuple[str, str] | None:
    """Recursively find the first non-empty string value for target keys."""
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return key, value
        for value in payload.values():
            found = _find_first_text(value, keys)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_first_text(item, keys)
            if found is not None:
                return found
    return None


def _clean_text(text: str) -> str:
    """Normalize context text by stripping markup, entities, and extra whitespace."""
    cleaned = strip_jats_xml(text)
    cleaned = re.sub(r"\s+", " ", html.unescape(cleaned)).strip()
    return cleaned
