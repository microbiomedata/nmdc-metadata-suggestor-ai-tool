"""Shared best-effort publication lookup for data DOI provider resolvers."""

from nmdc_metadata_suggestor_ai_tool.doi_ingestion.datacite import (
    DataciteAttributesCache,
    extract_related_publication_dois_from_datacite_attributes,
    fetch_datacite_attributes,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    merge_unique_strings,
    normalize_http_url,
)
from nmdc_metadata_suggestor_ai_tool.models.resolver_context import ResolverContext
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.retreive_pdf_link import (
    retrieve_pdf_link_from_crossref,
    retrieve_pdf_link_from_pmc,
)


def try_general_publication_lookup(
    doi: str,
    errors: list[str] | None = None,
    cache: DataciteAttributesCache | None = None,
) -> ResolverContext | None:
    """Return best-effort related paper DOI/PDF metadata from generic DOI resolvers."""
    publication_dois = _lookup_datacite_related_publication_dois(doi, errors=errors, cache=cache)
    if not publication_dois:
        return None

    publication_urls: list[str] | None = None
    for publication_doi in publication_dois:
        publication_urls = merge_unique_strings(
            publication_urls,
            _lookup_publication_urls(publication_doi),
        )

    return ResolverContext(
        text=None,
        raw_text=None,
        kind="publication_lookup",
        source="general_publication_lookup",
        urls=publication_urls,
        publication_dois=publication_dois,
    )


def _lookup_datacite_related_publication_dois(
    doi: str,
    errors: list[str] | None = None,
    cache: DataciteAttributesCache | None = None,
) -> list[str] | None:
    """Extract publication DOI relations from the generic DataCite record."""
    attrs = fetch_datacite_attributes(doi, errors=errors, cache=cache)
    if attrs is None:
        return None
    return extract_related_publication_dois_from_datacite_attributes(attrs, requested_doi=doi)


def _lookup_publication_urls(publication_doi: str) -> list[str] | None:
    """Resolve likely PDF URLs for a publication DOI through generic resolvers."""
    publication_urls: list[str] | None = None

    crossref_result = retrieve_pdf_link_from_crossref(publication_doi)
    for candidate in crossref_result.get("pdf_links", []):
        normalized = normalize_http_url(candidate)
        if normalized is None:
            continue
        publication_urls = merge_unique_strings(publication_urls, [normalized])

    pmc_result = retrieve_pdf_link_from_pmc(publication_doi)
    pmc_pdf_url = normalize_http_url(pmc_result.get("pdf_url"))
    if pmc_pdf_url is not None:
        publication_urls = merge_unique_strings(publication_urls, [pmc_pdf_url])

    return publication_urls
