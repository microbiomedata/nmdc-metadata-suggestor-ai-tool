"""DOI description/abstract ingestion for repository-backed DOI providers.

This module orchestrates source-specific DOI context resolvers and a generic
waterfall (DataCite, Crossref, content negotiation).
"""

from collections.abc import Callable

from nmdc_metadata_suggestor_ai_tool.constants import DOI_PATTERN
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.content_negotiation import (
    try_content_negotiation_context,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.crossref import try_crossref_context
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.cyverse import try_cyverse
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.datacite import try_datacite
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    classify_doi,
    infer_provider_from_doi,
    infer_provider_from_text,
    normalize_doi,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.edi import try_edi
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.emsl import try_emsl
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.ess_dive import try_ess_dive
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.figshare import try_figshare
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.jgi import try_jgi
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.kbase import try_kbase
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.massive import try_massive
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.openalex import try_openalex
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.osti import try_osti
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.osti import try_osti_award
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.pubmed import try_pubmed
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.zenodo import try_zenodo
from nmdc_metadata_suggestor_ai_tool.models.doi import DoiClassification, SourceRetrievalResult
from nmdc_metadata_suggestor_ai_tool.models.resolver_context import ResolverContext

SourceErrors = dict[str, str]
Fetcher = Callable[[str, str | None, list[str], SourceErrors], SourceRetrievalResult | None]
Resolver = Callable[[str, list[str] | None], ResolverContext | None]

PUBLICATION_API_SOURCES = (
    "datacite",
    "crossref",
    "content_negotiation",
    "openalex",
    "pubmed",
)
DATA_DOI_BASE_SOURCES = (
    "datacite",
    "crossref",
    "content_negotiation",
    "openalex",
    "pubmed",
)
NON_PUBLICATION_RESOURCE_TYPE_GENERAL = {
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
NON_PUBLICATION_CROSSREF_TYPES = {"component"}


def get_doi_description_or_abstract(
    doi: str, sources: list[str] | None = None, skip_classification: bool = False
) -> SourceRetrievalResult:
    """
    Fetch abstract/description text for a DOI using a source waterfall.

    Args:
        doi: A DOI string in any common format (bare, URL, ``doi:`` prefix).
        sources: Which sources to try, in order. Defaults to all available sources.
        skip_classification: If True, skip DOI classification/gating and go
            straight to source waterfall selection. When ``sources`` is
            explicitly provided, classification/gating is skipped regardless.

    Returns:
        SourceRetrievalResult with the abstract/description text and metadata, or an error.
    """
    doi = normalize_doi(doi)

    if not DOI_PATTERN.match(doi):
        return SourceRetrievalResult(doi=doi, error="Invalid DOI: Malformed DOI syntax")

    # check if we can infer the provider from the DOI prefix
    provider = infer_provider_from_doi(doi)

    classification: DoiClassification | None = None
    if not skip_classification and sources is None:
        classification = classify_doi(doi)
        if not classification.is_valid:
            return SourceRetrievalResult(
                doi=doi,
                error=f"Invalid DOI: {classification.error or 'validation failed'}",
            )
        refusal = _check_classification_gate(classification)
        if refusal:
            return SourceRetrievalResult(doi=doi, error=refusal)

    # choose waterfall based on DOI type (publication vs data DOI)
    if sources is None:
        sources = default_source_order(provider, classification)

    source_errors: SourceErrors = {}
    attempts: list[str] = []

    def merge_unique(existing: list[str] | None, incoming: list[str] | None) -> list[str] | None:
        """Merge two lists of strings while preserving order and deduplicating."""
        if not incoming:
            return existing
        return list(dict.fromkeys([*(existing or []), *incoming]))

    accumulated_publication_urls: list[str] | None = None
    accumulated_publication_dois: list[str] | None = None
    winning_result: SourceRetrievalResult | None = None
    # loop through sources in order
    # accumulate publication link metadata, but only stop once text is found
    for source in sources:
        fetcher = _SOURCE_FETCHERS.get(source)
        if fetcher is None:
            continue

        attempts = merge_unique(attempts, [source]) or attempts
        result = fetcher(doi, provider, attempts, source_errors)
        if result is None:
            continue

        attempts = merge_unique(attempts, result.attempts) or attempts

        if result.provider:
            provider = result.provider

        accumulated_publication_urls = merge_unique(
            accumulated_publication_urls,
            result.publication_urls,
        )
        accumulated_publication_dois = merge_unique(
            accumulated_publication_dois,
            result.publication_dois,
        )

        if result.context:
            winning_result = result
            break

    if winning_result:
        return SourceRetrievalResult(
            doi=doi,
            context=winning_result.context,
            raw_context=winning_result.raw_context,
            context_type=winning_result.context_type,
            publication_urls=accumulated_publication_urls,
            publication_dois=accumulated_publication_dois,
            provider=provider,
            source=winning_result.source,
            attempts=attempts,
            source_errors=source_errors,
            error=(
                "Source errors occurred. Check source_errors field for details."
                if source_errors
                else None
            ),
        )

    # if we got here, no sources returned usable summary text.
    # Return an error with details of all source failures for observability.
    return SourceRetrievalResult(
        doi=doi,
        publication_urls=accumulated_publication_urls,
        publication_dois=accumulated_publication_dois,
        provider=provider,
        attempts=attempts,
        source_errors=source_errors,
        error="No description or abstract found in any source. Check source_errors for details.",
    )


def _classification_is_publication(classification: DoiClassification | None) -> bool | None:
    """Return publication/data signal from classification, or None when inconclusive."""
    if classification is None:
        return None

    if classification.inferred_nmdc_category == "publication_doi":
        return True

    if classification.inferred_nmdc_category in {
        "dataset_doi",
        "award_doi",
        "data_management_plan_doi",
    }:
        return False

    if classification.resource_type_general in NON_PUBLICATION_RESOURCE_TYPE_GENERAL:
        return False

    if classification.resource_type in NON_PUBLICATION_CROSSREF_TYPES:
        return False

    return None


def _check_classification_gate(c: DoiClassification) -> str | None:
    """Refuse non-publication DOIs for publication abstract retrieval flow."""
    if c.inferred_nmdc_category and c.inferred_nmdc_category != "publication_doi":
        return (
            f"DOI is a {c.inferred_nmdc_category}, not a publication. "
            "Abstract retrieval is only supported for publication DOIs."
        )

    if c.resource_type_general and c.resource_type_general in NON_PUBLICATION_RESOURCE_TYPE_GENERAL:
        return (
            f"DOI has DataCite resourceTypeGeneral={c.resource_type_general!r}, "
            "which is not a publication type."
        )

    if c.resource_type and c.resource_type in NON_PUBLICATION_CROSSREF_TYPES:
        return f"DOI has Crossref type={c.resource_type!r}, which is not a publication type."

    return None


def default_source_order(
    provider: str | None,
    classification: DoiClassification | None = None,
) -> list[str]:
    """Return default source order based on DOI type and provider signal."""
    is_publication = _classification_is_publication(classification)

    if is_publication is True:
        if provider in _PROVIDER_API_SOURCES and provider not in PUBLICATION_API_SOURCES:
            return list(dict.fromkeys([provider, *PUBLICATION_API_SOURCES]))
        return list(PUBLICATION_API_SOURCES)

    if is_publication is False:
        sources: list[str] = []
        if provider in _PROVIDER_API_SOURCES:
            sources.append(provider)
        sources.extend(DATA_DOI_BASE_SOURCES)
        return list(dict.fromkeys(sources))

    if provider in _PROVIDER_API_SOURCES:
        if provider in PUBLICATION_API_SOURCES:
            return list(dict.fromkeys([provider, *PUBLICATION_API_SOURCES, *DATA_DOI_BASE_SOURCES]))
        return list(dict.fromkeys([provider, *DATA_DOI_BASE_SOURCES]))

    return list(PUBLICATION_API_SOURCES)


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
    if len(errors) > 0:
        _record_source_error(source_errors, source, errors)
    if context is None:
        return None
    result_source = context.source or source
    result_attempts = attempts
    if result_source not in result_attempts:
        result_attempts = [*result_attempts, result_source]
    return SourceRetrievalResult(
        doi=doi,
        context=context.text,
        raw_context=context.raw_text,
        context_type=context.kind,
        publication_urls=context.urls,
        publication_dois=context.publication_dois,
        provider=provider,
        source=result_source,
        attempts=result_attempts,
        source_errors=source_errors,
        error=(
            "Source errors occurred. Check source_errors field for details."
            if source_errors
            else None
        ),
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

    inferred_provider = provider or infer_provider_from_text(context.source)
    return SourceRetrievalResult(
        doi=doi,
        context=context.text,
        raw_context=context.raw_text,
        context_type=context.kind,
        publication_urls=context.urls,
        publication_dois=context.publication_dois,
        provider=inferred_provider,
        source="datacite",
        attempts=attempts,
        source_errors=source_errors,
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

    inferred_provider = provider or infer_provider_from_text(context.source)
    return SourceRetrievalResult(
        doi=doi,
        context=context.text,
        raw_context=context.raw_text,
        context_type=context.kind,
        publication_urls=context.urls,
        publication_dois=context.publication_dois,
        provider=inferred_provider,
        source="crossref",
        attempts=attempts,
        source_errors=source_errors,
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


def _fetch_osti(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from OSTI API."""
    return _fetch_resolver_context(doi, provider, "osti", attempts, source_errors, try_osti)

def _fetch_osti_award(
    doi: str, provider: str | None, attempts: list[str], source_errors: SourceErrors
) -> SourceRetrievalResult | None:
    """Fetch and wrap context from OSTI Award API."""
    return _fetch_resolver_context(doi, provider, "osti_award", attempts, source_errors, try_osti_award)


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
    "pubmed": _fetch_pubmed,
    "osti": _fetch_osti,
    "osti_award": _fetch_osti_award,
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
