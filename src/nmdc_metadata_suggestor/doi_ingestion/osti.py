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
from nmdc_metadata_suggestor.models.resolver_context import ResolverContext


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

    raw_description = record.get("description")
    abstract = None
    if isinstance(raw_description, str):
        abstract = clean_text(raw_description)
    else:
        append_error(errors, "No abstract/description found in OSTI record")

    pdf_url: list[str] = []
    ja_doi = record.get("doi")
    if record.get("product_type") == "Journal Article":
        links = record.get("links")
        if isinstance(links, list):
            for link in links:
                if not isinstance(link, dict):
                    continue
                if link.get("rel") == "fulltext":
                    href = link.get("href")
                    if isinstance(href, str) and href.strip():
                        pdf_url.append(href.strip())
                        break

    if not abstract and not pdf_url:
        append_error(errors, "OSTI record contained no abstract/description or PDF link")
        return None

    return ResolverContext(
        text=abstract,
        raw_text=raw_description if isinstance(raw_description, str) else None,
        kind="description",
        source="osti",
        urls=pdf_url if pdf_url else None,
        publication_dois=[ja_doi] if isinstance(ja_doi, str) and doi not in ja_doi else None,
    )


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
