"""Shared best-effort publication lookup for data DOI provider resolvers."""

import requests

from nmdc_metadata_suggestor.constants import DATACITE_API_URL, DEFAULT_TIMEOUT, USER_AGENT
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    extract_related_publication_dois,
    merge_unique_strings,
    normalize_http_url,
    request_with_retry,
)
from nmdc_metadata_suggestor.models.resolver_context import ResolverContext
from nmdc_metadata_suggestor.publication_ingestion.retreive_pdf_link import (
    retrieve_pdf_link_from_crossref,
    retrieve_pdf_link_from_pmc,
)


def try_general_publication_lookup(
    doi: str, errors: list[str] | None = None
) -> ResolverContext | None:
    """Return best-effort related paper DOI/PDF metadata from generic DOI resolvers."""
    del errors

    publication_dois = _lookup_datacite_related_publication_dois(doi)
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


def _lookup_datacite_related_publication_dois(doi: str) -> list[str] | None:
    """Extract publication DOI relations from the generic DataCite record."""
    try:
        response = request_with_retry(
            "GET",
            f"{DATACITE_API_URL}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        attrs = response.json().get("data", {}).get("attributes", {})
    except (requests.RequestException, ValueError):
        return None

    related_identifiers = attrs.get("relatedIdentifiers")
    return extract_related_publication_dois(related_identifiers, requested_doi=doi) or None


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
