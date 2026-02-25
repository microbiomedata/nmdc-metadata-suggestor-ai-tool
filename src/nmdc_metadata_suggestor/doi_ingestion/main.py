"""DOI description/abstract ingestion for repository-backed DOI providers.

This module orchestrates source-specific DOI context resolvers and a generic
waterfall (DataCite, Crossref, content negotiation).
"""

from collections.abc import Callable

from nmdc_metadata_suggestor.constants import (
    DEFAULT_TIMEOUT,
    DOI_PATTERN,
    USER_AGENT,
)

from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    normalize_doi,
    classify_doi,
)
from nmdc_metadata_suggestor.doi_ingestion.content_negotiation import (
    try_content_negotiation_context,
)
from nmdc_metadata_suggestor.doi_ingestion.crossref import try_crossref_context
from nmdc_metadata_suggestor.doi_ingestion.datacite import try_datacite
from nmdc_metadata_suggestor.doi_ingestion.cyverse import try_cyverse
from nmdc_metadata_suggestor.doi_ingestion.edi import try_edi
from nmdc_metadata_suggestor.doi_ingestion.emsl import try_emsl
from nmdc_metadata_suggestor.doi_ingestion.ess_dive import try_ess_dive
from nmdc_metadata_suggestor.doi_ingestion.figshare import try_figshare
from nmdc_metadata_suggestor.doi_ingestion.jgi import try_jgi
from nmdc_metadata_suggestor.doi_ingestion.kbase import try_kbase
from nmdc_metadata_suggestor.doi_ingestion.massive import try_massive
from nmdc_metadata_suggestor.doi_ingestion.resolver_context import ResolverContext
from nmdc_metadata_suggestor.doi_ingestion.zenodo import try_zenodo
from nmdc_metadata_suggestor.doi_ingestion.openalex import try_openalex
from nmdc_metadata_suggestor.doi_ingestion.pubmed import try_pubmed

from nmdc_metadata_suggestor.models.doi import DoiClassification, SourceRetrievalResult

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

SourceErrors = dict[str, str]
Fetcher = Callable[[str, str | None, list[str], SourceErrors], SourceRetrievalResult | None]
Resolver = Callable[[str, list[str] | None], ResolverContext | None]


def get_doi_description_or_abstract(
    doi: str, sources: list[str] | None = None, skip_classification: bool = False
) -> SourceRetrievalResult:
    """
    Fetch abstract/description text for a DOI using a source waterfall.

    Args:
        doi: A DOI string in any common format (bare, URL, ``doi:`` prefix).
        sources: Which sources to try, in order. Defaults to all available sources.
        skip_classification: If True, skip DOI classification and go straight
            to the waterfall. Useful when the caller has already verified the
            DOI type.

    Returns:
        SourceRetrievalResult with the abstract/description text and metadata, or an error.
    """
    doi = normalize_doi(doi)
    if not DOI_PATTERN.match(doi):
        return SourceRetrievalResult(doi=doi, error="Invalid DOI: Malformed DOI syntax")

    provider = _infer_provider_from_doi(doi)
    if sources is None:
        sources = _default_source_order(provider)

    if not skip_classification:
        classification = classify_doi(doi)
        refusal = _check_classification_gate(classification)
        if refusal:
            return SourceRetrievalResult(doi=doi, error=refusal)

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

    return SourceRetrievalResult(
        doi=doi,
        provider=provider,
        attempts=attempts,
        source_errors=source_errors,
        error="No description or abstract found in any source",
    )


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
    # DataCite resourceTypeGeneral values that are clearly not publications.
    # These are unmapped in doi_utils (inferred_nmdc_category returns None),
    # but we can still refuse them in the abstract retrieval gate.
    # Source: DataCite Metadata Schema 4.6
    datacite_non_publication_types = [
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
    ]
    # Crossref work types that are clearly not publications.
    # "component" is a figure/table/supplement within a larger work.
    crossref_non_publication_types = ["component"]
    if not c.is_valid:
        return f"Invalid DOI: {c.error or 'validation failed'}"

    # Check NMDC category (most specific signal)
    if c.inferred_nmdc_category and c.inferred_nmdc_category != "publication_doi":
        return (
            f"DOI is a {c.inferred_nmdc_category}, not a publication. "
            "Abstract retrieval is only supported for publication DOIs."
        )

    # Check DataCite resourceTypeGeneral (catches unmapped non-publications)
    if c.resource_type_general and c.resource_type_general in datacite_non_publication_types:
        return (
            f"DOI has DataCite resourceTypeGeneral={c.resource_type_general!r}, "
            "which is not a publication type."
        )

    # Check Crossref type (catches unmapped non-publications)
    if c.resource_type and c.resource_type in crossref_non_publication_types:
        return f"DOI has Crossref type={c.resource_type!r}, which is not a publication type."

    return None



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
) -> SourceRetrievalResult:
    """Construct a normalized context result object."""
    return SourceRetrievalResult(
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


def _fetch_resolver_context(
    doi: str,
    provider: str | None,
    source: str,
    attempts: list[str],
    source_errors: SourceErrors,
    resolver: Resolver,
) -> SourceRetrievalResult | None:
    """Run a resolver and normalize successful context into a result object."""
    errors: list[str] = []
    context = resolver(doi, errors)
    if context is None:
        _record_source_error(source_errors, source, errors)
        return None
    result_source = context.source or source
    return _build_result(
        doi,
        provider,
        result_source,
        attempts,
        source_errors,
        context.text,
        context.raw_text,
        context.kind,
    )


def _fetch_ess_dive(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from ESS-DIVE-specific API."""
    return _fetch_resolver_context(doi, provider, "ess-dive", attempts, source_errors, try_ess_dive)


def _fetch_edi(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from EDI's PASTA API."""
    return _fetch_resolver_context(doi, provider, "edi", attempts, source_errors, try_edi)


def _fetch_emsl(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from EMSL project API."""
    return _fetch_resolver_context(doi, provider, "emsl", attempts, source_errors, try_emsl)


def _fetch_figshare(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from Figshare-specific API."""
    return _fetch_resolver_context(doi, provider, "figshare", attempts, source_errors, try_figshare)


def _fetch_jgi(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from JGI search API."""
    return _fetch_resolver_context(doi, provider, "jgi", attempts, source_errors, try_jgi)


def _fetch_kbase(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from KBase APIs."""
    return _fetch_resolver_context(doi, provider, "kbase", attempts, source_errors, try_kbase)


def _fetch_massive(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from MassIVE via ProteomeCentral PROXI."""
    return _fetch_resolver_context(doi, provider, "massive", attempts, source_errors, try_massive)


def _fetch_cyverse(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from CyVerse Terrain metadata APIs."""
    return _fetch_resolver_context(doi, provider, "cyverse", attempts, source_errors, try_cyverse)


def _fetch_zenodo(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from Zenodo-specific API."""
    return _fetch_resolver_context(doi, provider, "zenodo", attempts, source_errors, try_zenodo)


def _fetch_datacite(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from DataCite metadata."""
    errors: list[str] = []
    context = try_datacite(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "datacite", errors)
        return None

    inferred_provider = provider or _infer_provider_from_text(context.source)
    return _build_result(
        doi, inferred_provider, "datacite", attempts, source_errors,
        context.text, context.raw_text, context.kind
    )


def _fetch_crossref(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from Crossref metadata."""
    errors: list[str] = []
    context = try_crossref_context(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "crossref", errors)
        return None

    inferred_provider = provider or _infer_provider_from_text(context.source)
    return _build_result(
        doi, inferred_provider, "crossref", attempts, source_errors,
        context.text, context.raw_text, context.kind
    )


def _fetch_content_negotiation(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context via DOI content negotiation."""
    return _fetch_resolver_context(
        doi,
        provider,
        "content_negotiation",
        attempts,
        source_errors,
        try_content_negotiation_context,
    )

def _fetch_openalex(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from OpenAlex metadata."""
    return _fetch_resolver_context(doi, provider, "openalex", attempts, source_errors, try_openalex)

def _fetch_pubmed(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from PubMed via NCBI APIs."""
    return _fetch_resolver_context(doi, provider, "pubmed", attempts, source_errors, try_pubmed)

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
    "openalex": _fetch_openalex,
    "crossref": _fetch_crossref,
    "pubmed": _fetch_pubmed,
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


