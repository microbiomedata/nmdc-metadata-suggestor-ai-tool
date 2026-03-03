"""
A module to attempt to retrieve publication abstract and PDF bytes via a provided OSTI DOI.

This module provides direct API-based retrieval of OSTI DOIs abstracts
and full-text PDFs links starting from OSTI, then calling Crossref and/or Europe PMC.
"""

import requests

from nmdc_metadata_suggestor.constants import (
    DEFAULT_TIMEOUT,
    OSTI_API_URL,
    OSTI_E2_API_URL,
    USER_AGENT,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    request_with_retry,
)
from nmdc_metadata_suggestor.models.publication import Publication
from nmdc_metadata_suggestor.models.resolver_context import ResolverContext
from nmdc_metadata_suggestor.publication_ingestion.retreive_pdf_link import (
    retrieve_pdf_link_from_crossref,
    retrieve_pdf_link_from_pmc,
)


def try_osti(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return abstract/description context from OSTI if available."""
    try:
        data = query_osti_by_doi(osti_doi=doi)
    except requests.RequestException as exc:
        append_error(errors, f"OSTI API request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(errors, "OSTI API returned invalid JSON")
        return None

    if not data or len(data) == 0:
        append_error(errors, f"No publication found in OSTI for DOI: {doi}")
        return None

    record = data[0] if isinstance(data, list) else data
    abstract = record.get("description")
    if not isinstance(abstract, str):
        append_error(errors, "No abstract/description found in OSTI record")
        return None

    cleaned = clean_text(abstract)
    if not cleaned:
        append_error(errors, "No abstract/description found in OSTI record")
        return None

    return ResolverContext(text=cleaned, raw_text=abstract, kind="description", source="osti")


def query_osti_by_doi(osti_doi: str) -> dict:
    """
    Query OSTI API for a given DOI and return the JSON response.
    First queries the new E2 API, and if that fails, falls back to the original API.
    """
    params = {
        "doi": osti_doi,
    }
    headers = {"User-Agent": USER_AGENT}
    try:
        osti_url = f"{OSTI_E2_API_URL}"
        response = request_with_retry(
            "GET",
            osti_url,
            timeout=DEFAULT_TIMEOUT,
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()  # type: ignore

    except requests.RequestException:
        # Fallback to the original API if the E2 API fails
        osti_url = f"{OSTI_API_URL}"

        response = request_with_retry(
            "GET",
            osti_url,
            timeout=DEFAULT_TIMEOUT,
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()  # type: ignore


def retrieve_pdf_link_from_osti_doi(doi: str) -> Publication:
    """Retrieve publication information including PDF links from OSTI API using a DOI.

    Args:
        doi: Digital Object Identifier (e.g., "10.15485/1729719")

    Returns:
        Publication object with abstract, URLs, and associated publication DOI.
        If an error occurs, returns Publication with error field populated.
    """
    try:
        data = query_osti_by_doi(doi)

        # Check if we got any results
        if not data or len(data) == 0:
            return Publication(doi=doi, error=f"No publication found in OSTI for DOI: {doi}")

        # get the record
        record = data[0] if isinstance(data, list) else data

        if record.get("product_type") == "Journal Article":
            # check if osti has a direct PDF link (only for journal articles)
            if record.get("links"):
                for link in record["links"]:
                    if link.get("rel") == "fulltext":
                        return Publication(
                            source="osti",
                            doi=doi,
                            urls=[link.get("href")],
                            abstract=record.get("description"),
                        )
            # get the JA DOI
            ja_doi = record.get("doi")
            # we can see if the record is a publication or not. If not, return the description.
            if not ja_doi:
                return Publication(doi=doi, error="Journal Article record missing DOI")

            crossref_pdf_links = retrieve_pdf_link_from_crossref(ja_doi)

            if len(crossref_pdf_links.get("pdf_links", [])) != 0:
                pub = Publication(
                    source="Crossref",
                    doi=doi,
                    associated_publication_doi=ja_doi,
                    urls=crossref_pdf_links.get("pdf_links"),
                    abstract=record.get("description"),
                )
            else:
                # try pmc
                pmc_pdf_info = retrieve_pdf_link_from_pmc(ja_doi)
                pdf_url = pmc_pdf_info.get("pdf_url")
                pub = Publication(
                    source="PMC" if pdf_url else None,
                    doi=doi,
                    associated_publication_doi=ja_doi,
                    pmid=pmc_pdf_info.get("pmid"),
                    urls=[pdf_url] if pdf_url else None,  # type: ignore[list-item]
                    abstract=record.get("description"),
                )
            return pub
        else:
            return Publication(doi=doi, abstract=record.get("description"))

    except Exception as e:
        return Publication(doi=doi, error=f"Failed to retrieve DOI information from OSTI: {str(e)}")
