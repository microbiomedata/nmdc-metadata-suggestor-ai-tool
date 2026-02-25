"""DOI description/abstract ingestion for repository-backed DOI providers.

This module orchestrates source-specific DOI context resolvers and a generic
waterfall (DataCite, Crossref, content negotiation).
"""

from collections.abc import Callable

from nmdc_metadata_suggestor.constants import DOI_PATTERN

from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    normalize_doi,
    classify_doi,
    infer_provider_from_text,
    infer_provider_from_doi,
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
from nmdc_metadata_suggestor.models.resolver_context import ResolverContext
from nmdc_metadata_suggestor.doi_ingestion.zenodo import try_zenodo
from nmdc_metadata_suggestor.doi_ingestion.openalex import try_openalex
from nmdc_metadata_suggestor.doi_ingestion.pubmed import try_pubmed
from nmdc_metadata_suggestor.doi_ingestion.osti import try_osti

from nmdc_metadata_suggestor.models.doi import DoiClassification, SourceRetrievalResult

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
    
    # check if we can infer the provider from the DOI prefix
    provider = infer_provider_from_doi(doi)
    
    # use the provider-aware default source order if we have a provider hint, otherwise use the generic default order
    if sources is None:
        sources = default_source_order(provider)

    if not skip_classification:
        classification = classify_doi(doi)
        refusal = _check_classification_gate(classification)
        if refusal:
            return SourceRetrievalResult(doi=doi, error=refusal)

    source_errors: SourceErrors = {}
    # loop through sources in order and return the first successful result, recording errors for observability
    for source in sources:
        fetcher = _SOURCE_FETCHERS.get(source)
        if fetcher is None:
            continue
        result = fetcher(doi, provider, source_errors)
        if result is not None:
            return result
    # if we got here, no sources returned a result. Return an error with details of all source failures for observability.
    return SourceRetrievalResult(
        doi=doi,
        provider=provider,
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


def default_source_order(provider: str | None) -> list[str]:
    """Return the default source order for a provider-aware lookup."""
    if provider in _PROVIDER_API_SOURCES:
        return [provider, "datacite", "crossref", "content_negotiation", "openalex", "pubmed"]
    return ["datacite", "crossref", "content_negotiation", "openalex", "pubmed"]


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
    return SourceRetrievalResult(
        doi=doi,
        content=context.text,
        raw_content=context.raw_text,
        content_type=context.kind,
        provider=provider,
        source=result_source,
        source_errors=source_errors,
    )


def _fetch_ess_dive(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from ESS-DIVE-specific API."""
    return _fetch_resolver_context(doi, provider, "ess-dive", source_errors, try_ess_dive)


def _fetch_edi(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from EDI's PASTA API."""
    return _fetch_resolver_context(doi, provider, "edi", source_errors, try_edi)


def _fetch_emsl(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from EMSL project API."""
    return _fetch_resolver_context(doi, provider, "emsl", source_errors, try_emsl)


def _fetch_figshare(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from Figshare-specific API."""
    return _fetch_resolver_context(doi, provider, "figshare", source_errors, try_figshare)


def _fetch_jgi(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from JGI search API."""
    return _fetch_resolver_context(doi, provider, "jgi", source_errors, try_jgi)


def _fetch_kbase(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from KBase APIs."""
    return _fetch_resolver_context(doi, provider, "kbase", source_errors, try_kbase)


def _fetch_massive(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from MassIVE via ProteomeCentral PROXI."""
    return _fetch_resolver_context(doi, provider, "massive", source_errors, try_massive)


def _fetch_cyverse(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from CyVerse Terrain metadata APIs."""
    return _fetch_resolver_context(doi, provider, "cyverse", source_errors, try_cyverse)


def _fetch_zenodo(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from Zenodo-specific API."""
    return _fetch_resolver_context(doi, provider, "zenodo", source_errors, try_zenodo)


def _fetch_datacite(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from DataCite metadata."""
    errors: list[str] = []
    context = try_datacite(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "datacite", errors)
        return None

    inferred_provider = provider or infer_provider_from_text(context.source)
    return SourceRetrievalResult(
        doi=doi,
        content=context.text,
        raw_content=context.raw_text,
        content_type=context.kind,
        provider=inferred_provider,
        source="datacite",
        source_errors=source_errors,
    )


def _fetch_crossref(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from Crossref metadata."""
    errors: list[str] = []
    context = try_crossref_context(doi, errors=errors)
    if context is None:
        _record_source_error(source_errors, "crossref", errors)
        return None

    inferred_provider = provider or infer_provider_from_text(context.source)
    return SourceRetrievalResult(
        doi=doi,
        content=context.text,
        raw_content=context.raw_text,
        content_type=context.kind,
        provider=inferred_provider,
        source="crossref",
        source_errors=source_errors,
    )


def _fetch_content_negotiation(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context via DOI content negotiation."""
    return _fetch_resolver_context(
        doi,
        provider,
        "content_negotiation",
        source_errors,
        try_content_negotiation_context,
    )

def _fetch_openalex(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from OpenAlex metadata."""
    return _fetch_resolver_context(doi, provider, "openalex", source_errors, try_openalex)

def _fetch_pubmed(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from PubMed via NCBI APIs."""
    return _fetch_resolver_context(doi, provider, "pubmed", source_errors, try_pubmed)

def _fetch_osti(
    doi: str, provider: str | None, source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from OSTI API."""
    return try_osti(doi)

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
    "osti": _fetch_osti,
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
    "osti",
}


