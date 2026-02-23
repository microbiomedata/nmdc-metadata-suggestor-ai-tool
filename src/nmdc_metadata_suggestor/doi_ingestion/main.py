"""DOI description/abstract ingestion for repository-backed DOI providers.

This module orchestrates source-specific DOI context resolvers and a generic
waterfall (DataCite, Crossref, content negotiation).
"""

from collections.abc import Callable

import requests

import nmdc_metadata_suggestor.doi_ingestion.cyverse as cyverse_resolver
import nmdc_metadata_suggestor.doi_ingestion.edi as edi_resolver
import nmdc_metadata_suggestor.doi_ingestion.emsl as emsl_resolver
import nmdc_metadata_suggestor.doi_ingestion.ess_dive as ess_dive_resolver
import nmdc_metadata_suggestor.doi_ingestion.figshare as figshare_resolver
import nmdc_metadata_suggestor.doi_ingestion.jgi as jgi_resolver
import nmdc_metadata_suggestor.doi_ingestion.kbase as kbase_resolver
import nmdc_metadata_suggestor.doi_ingestion.massive as massive_resolver
import nmdc_metadata_suggestor.doi_ingestion.zenodo as zenodo_resolver
from nmdc_metadata_suggestor.doi_ingestion.common import clean_text
from nmdc_metadata_suggestor.doi_ingestion.cyverse import try_cyverse
from nmdc_metadata_suggestor.doi_ingestion.edi import try_edi
from nmdc_metadata_suggestor.doi_ingestion.emsl import try_emsl
from nmdc_metadata_suggestor.doi_ingestion.ess_dive import try_ess_dive
from nmdc_metadata_suggestor.doi_ingestion.figshare import try_figshare
from nmdc_metadata_suggestor.doi_ingestion.jgi import try_jgi
from nmdc_metadata_suggestor.doi_ingestion.kbase import try_kbase
from nmdc_metadata_suggestor.doi_ingestion.massive import try_massive
from nmdc_metadata_suggestor.doi_ingestion.zenodo import try_zenodo
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

# Re-export endpoint constants for tests and external callers.
EDI_DOI_API = edi_resolver.EDI_DOI_API
EMSL_PROJECTS_API = emsl_resolver.EMSL_PROJECTS_API
ESS_DIVE_API = ess_dive_resolver.ESS_DIVE_API
DATAONE_CN_SOLR_API = ess_dive_resolver.DATAONE_CN_SOLR_API
FIGSHARE_API = figshare_resolver.FIGSHARE_API
FIGSHARE_COLLECTIONS_API = figshare_resolver.FIGSHARE_COLLECTIONS_API
JGI_SEARCH_API = jgi_resolver.JGI_SEARCH_API
KBASE_SEARCH_API = kbase_resolver.KBASE_SEARCH_API
KBASE_WORKSPACE_API = kbase_resolver.KBASE_WORKSPACE_API
DOI_CONTENT_NEGOTIATION_API = massive_resolver.DOI_CONTENT_NEGOTIATION_API
PROXI_DATASETS_API = massive_resolver.PROXI_DATASETS_API
CYVERSE_METADATA_API = cyverse_resolver.CYVERSE_METADATA_API
CYVERSE_METADATA_SEARCH_API = cyverse_resolver.CYVERSE_METADATA_SEARCH_API
ZENODO_API = zenodo_resolver.ZENODO_API

TARGET_PROVIDER_PREFIXES: dict[str, str] = {
    "10.6073": "edi",
    "10.46936": "emsl",
    "10.15485": "ess-dive",
    "10.21952": "ess-dive",
    "10.6084": "figshare",
    "10.25585": "jgi",
    "10.25982": "kbase",
    "10.25345": "massive",
    "10.17504": "cyverse",
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
    "edi",
    "emsl",
    "ess-dive",
    "figshare",
    "jgi",
    "kbase",
    "massive",
    "cyverse",
    "zenodo",
    "datacite",
    "crossref",
    "content_negotiation",
)


SourceErrors = dict[str, str]
Fetcher = Callable[[str, str | None, list[str], SourceErrors], DoiContextResult | None]


def get_doi_description_or_abstract(
    doi: str, sources: list[str] | None = None
) -> DoiContextResult:
    """Fetch abstract/description text for a DOI using a source waterfall."""
    doi = normalize_doi(doi)
    if not DOI_PATTERN.match(doi):
        return DoiContextResult(doi=doi, error="Invalid DOI: Malformed DOI syntax")

    provider = _infer_provider_from_doi(doi)
    if sources is None:
        sources = _default_source_order(provider)

    attempts: list[str] = []
    source_errors: SourceErrors = {}
    for source in sources:
        fetcher = _SOURCE_FETCHERS.get(source)
        if fetcher is None:
            continue
        attempts.append(source)
        result = fetcher(doi, provider, attempts, source_errors)
        if result is not None:
            return result

    return DoiContextResult(
        doi=doi,
        provider=provider,
        attempts=attempts,
        source_errors=source_errors,
        error="No description or abstract found in any source",
    )


def _default_source_order(provider: str | None) -> list[str]:
    """Return the default source order for a provider-aware lookup."""
    if provider in _PROVIDER_API_SOURCES:
        return [provider, "datacite", "crossref", "content_negotiation"]
    return ["datacite", "crossref", "content_negotiation"]


def _infer_provider_from_doi(doi: str) -> str | None:
    """Infer a provider key from DOI prefix mapping."""
    prefix = doi.split("/", 1)[0] if "/" in doi else ""
    return TARGET_PROVIDER_PREFIXES.get(prefix)


def _infer_provider_from_text(text: str | None) -> str | None:
    """Infer provider from publisher/source text keywords."""
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
    source_errors: SourceErrors,
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
        source_errors=source_errors,
    )


def _record_source_error(
    source_errors: SourceErrors, source: str, errors: list[str] | None
) -> None:
    """Store joined per-source error details for downstream observability."""
    if not errors:
        return
    source_errors[source] = "; ".join(errors)


def _fetch_ess_dive(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> DoiContextResult | None:
    """Fetch and wrap context from ESS-DIVE-specific API."""
    errors: list[str] = []
    context = try_ess_dive(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "ess-dive", errors)
        return None
    text, kind = context
    return _build_result(doi, provider, "ess-dive", attempts, source_errors, text, text, kind)


def _fetch_edi(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> DoiContextResult | None:
    """Fetch and wrap context from EDI's PASTA API."""
    errors: list[str] = []
    context = try_edi(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "edi", errors)
        return None
    text, kind = context
    return _build_result(doi, provider, "edi", attempts, source_errors, text, text, kind)


def _fetch_emsl(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> DoiContextResult | None:
    """Fetch and wrap context from EMSL project API."""
    errors: list[str] = []
    context = try_emsl(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "emsl", errors)
        return None
    text, raw, kind = context
    return _build_result(doi, provider, "emsl", attempts, source_errors, text, raw, kind)


def _fetch_figshare(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> DoiContextResult | None:
    """Fetch and wrap context from Figshare-specific API."""
    errors: list[str] = []
    text = try_figshare(doi, errors=errors)
    if text is None:
        _record_source_error(source_errors, "figshare", errors)
        return None
    return _build_result(
        doi, provider, "figshare", attempts, source_errors, text, text, "description"
    )


def _fetch_jgi(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> DoiContextResult | None:
    """Fetch and wrap context from JGI search API."""
    errors: list[str] = []
    context = try_jgi(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "jgi", errors)
        return None
    text, raw, kind = context
    return _build_result(doi, provider, "jgi", attempts, source_errors, text, raw, kind)


def _fetch_kbase(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> DoiContextResult | None:
    """Fetch and wrap context from KBase APIs."""
    errors: list[str] = []
    context = try_kbase(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "kbase", errors)
        return None
    text, raw, kind = context
    return _build_result(doi, provider, "kbase", attempts, source_errors, text, raw, kind)


def _fetch_massive(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> DoiContextResult | None:
    """Fetch and wrap context from MassIVE via ProteomeCentral PROXI."""
    errors: list[str] = []
    context = try_massive(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "massive", errors)
        return None
    text, raw, kind = context
    return _build_result(doi, provider, "massive", attempts, source_errors, text, raw, kind)


def _fetch_cyverse(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> DoiContextResult | None:
    """Fetch and wrap context from CyVerse Terrain metadata APIs."""
    errors: list[str] = []
    context = try_cyverse(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "cyverse", errors)
        return None
    text, raw, kind = context
    return _build_result(doi, provider, "cyverse", attempts, source_errors, text, raw, kind)


def _fetch_zenodo(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> DoiContextResult | None:
    """Fetch and wrap context from Zenodo-specific API."""
    errors: list[str] = []
    text = try_zenodo(doi, errors=errors)
    if text is None:
        _record_source_error(source_errors, "zenodo", errors)
        return None
    return _build_result(
        doi, provider, "zenodo", attempts, source_errors, text, text, "description"
    )


def _fetch_datacite(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> DoiContextResult | None:
    """Fetch and wrap context from DataCite metadata."""
    errors: list[str] = []
    context = _try_datacite(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "datacite", errors)
        return None

    text, raw, kind, publisher = context
    inferred_provider = provider or _infer_provider_from_text(publisher)
    return _build_result(
        doi, inferred_provider, "datacite", attempts, source_errors, text, raw, kind
    )


def _fetch_crossref(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> DoiContextResult | None:
    """Fetch and wrap context from Crossref metadata."""
    errors: list[str] = []
    context = _try_crossref(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "crossref", errors)
        return None

    text, raw, kind, publisher = context
    inferred_provider = provider or _infer_provider_from_text(publisher)
    return _build_result(
        doi, inferred_provider, "crossref", attempts, source_errors, text, raw, kind
    )


def _fetch_content_negotiation(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> DoiContextResult | None:
    """Fetch and wrap context via DOI content negotiation."""
    errors: list[str] = []
    context = _try_content_negotiation(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "content_negotiation", errors)
        return None
    text, raw, kind = context
    return _build_result(
        doi, provider, "content_negotiation", attempts, source_errors, text, raw, kind
    )


_SOURCE_FETCHERS: dict[str, Fetcher] = {
    "edi": _fetch_edi,
    "emsl": _fetch_emsl,
    "ess-dive": _fetch_ess_dive,
    "figshare": _fetch_figshare,
    "jgi": _fetch_jgi,
    "kbase": _fetch_kbase,
    "massive": _fetch_massive,
    "cyverse": _fetch_cyverse,
    "zenodo": _fetch_zenodo,
    "datacite": _fetch_datacite,
    "crossref": _fetch_crossref,
    "content_negotiation": _fetch_content_negotiation,
}

_PROVIDER_API_SOURCES = {
    "edi",
    "emsl",
    "ess-dive",
    "figshare",
    "jgi",
    "kbase",
    "massive",
    "cyverse",
    "zenodo",
}


def _try_datacite(
    doi: str, errors: list[str] | None = None
) -> tuple[str, str, str, str | None] | None:
    """Return cleaned/raw text from DataCite descriptions, preferring abstract."""
    try:
        response = requests.get(
            f"{DATACITE_API}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            if errors is not None:
                errors.append(f"DataCite API returned HTTP {response.status_code}")
            return None
        attrs = response.json().get("data", {}).get("attributes", {})
        descriptions = attrs.get("descriptions")
        publisher = attrs.get("publisher")
        if not isinstance(descriptions, list):
            if errors is not None:
                errors.append("DataCite response missing descriptions")
            return None
    except requests.RequestException as exc:
        if errors is not None:
            errors.append(f"DataCite API request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        if errors is not None:
            errors.append("DataCite API returned invalid JSON")
        return None

    preferred = _pick_datacite_description(descriptions)
    if preferred is None:
        if errors is not None:
            errors.append("DataCite contained no usable description entries")
        return None
    raw, kind = preferred
    cleaned = clean_text(raw)
    if not cleaned:
        if errors is not None:
            errors.append("DataCite context was empty after cleaning")
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


def _try_crossref(
    doi: str, errors: list[str] | None = None
) -> tuple[str, str, str, str | None] | None:
    """Return Crossref abstract text and publisher metadata if available."""
    try:
        response = requests.get(
            f"{CROSSREF_API}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            if errors is not None:
                errors.append(f"Crossref API returned HTTP {response.status_code}")
            return None
        message = response.json().get("message", {})
    except requests.RequestException as exc:
        if errors is not None:
            errors.append(f"Crossref API request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        if errors is not None:
            errors.append("Crossref API returned invalid JSON")
        return None

    raw = message.get("abstract")
    publisher = message.get("publisher")
    if not isinstance(raw, str) or not raw.strip():
        if errors is not None:
            errors.append("Crossref response contained no abstract")
        return None
    cleaned = strip_jats_xml(raw)
    if not cleaned:
        if errors is not None:
            errors.append("Crossref abstract was empty after cleaning")
        return None
    return cleaned, raw, "abstract", publisher if isinstance(publisher, str) else None


def _try_content_negotiation(
    doi: str, errors: list[str] | None = None
) -> tuple[str, str, str] | None:
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
            if errors is not None:
                errors.append(f"DOI content negotiation returned HTTP {response.status_code}")
            return None
        payload = response.json()
    except requests.RequestException as exc:
        if errors is not None:
            errors.append(f"DOI content negotiation request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        if errors is not None:
            errors.append("DOI content negotiation returned invalid JSON")
        return None

    abstract = payload.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        cleaned = strip_jats_xml(abstract)
        if cleaned:
            return cleaned, abstract, "abstract"

    note = payload.get("note")
    if isinstance(note, str) and note.strip():
        cleaned = clean_text(note)
        if cleaned:
            return cleaned, note, "description"
    if errors is not None:
        errors.append("DOI content negotiation contained no abstract/note")
    return None
