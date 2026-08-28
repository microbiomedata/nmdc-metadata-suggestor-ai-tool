"""
A module to attempt to retrieve publication's abstract and PDF bytes or an award's description
 via a provided OSTI DOI or OSTI award DOI.

This module provides direct API-based retrieval of OSTI DOIs abstracts
and full-text PDFs links starting from OSTI, then calling Crossref and/or Europe PMC.
It also provides direct API-based retrieval of OSTI award DOIs descriptions.
"""

import requests

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_TIMEOUT,
    OSTI_API_URL,
    OSTI_E2_API_URL,
    OSTI_AWARD_API_URL,
    USER_AGENT,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    request_with_retry,
)
from nmdc_metadata_suggestor_ai_tool.models.resolver_context import ResolverContext


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

    abstract = None
    pdf_urls: list[str] = []
    # osti can have multiple records for a single DOI

    raw_description = data[0].get("description")
    if isinstance(raw_description, str):
        abstract = clean_text(raw_description)
    else:
        append_error(errors, "No abstract/description found in OSTI record")

    # multiple records for a single DOI are possible in OSTI,
    # so we loop through all and aggregate PDF links
    ja_dois: list[str] = []
    for record in data:
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
                            pdf_urls.append(href.strip())
                            if isinstance(ja_doi, str):
                                ja_dois.append(ja_doi.strip())

    if not abstract and not pdf_urls:
        append_error(errors, "OSTI record contained no abstract/description or PDF link")
        return None

    return ResolverContext(
        text=abstract,
        raw_text=raw_description if isinstance(raw_description, str) else None,
        kind="description",
        source="osti",
        urls=pdf_urls if pdf_urls else None,
        publication_dois=[d for d in ja_dois if d != doi] if ja_dois else None,
    )


def query_osti_by_doi(osti_doi: str) -> list[dict]:
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


def query_osti_by_award_doi(award_doi: str) -> list[dict]:
    """
    Query OSTI award API for a given award DOI and return the JSON response.
    """
    params = {
        "award_doi": award_doi,
    }
    headers = {"User-Agent": USER_AGENT}
    osti_url = f"{OSTI_AWARD_API_URL}"
    response = request_with_retry(
        "GET",
        osti_url,
        timeout=DEFAULT_TIMEOUT,
        headers=headers,
        params=params,
    )
    response.raise_for_status()
    return response.json()  # type: ignore


def try_osti_award(award_doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return abstract/description context from OSTI award API if available."""
    try:
        data = query_osti_by_award_doi(award_doi=award_doi)
    except requests.RequestException as exc:
        append_error(errors, f"OSTI Award API request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(errors, "OSTI Award API returned invalid JSON")
        return None

    if not data or len(data) == 0:
        append_error(errors, f"No award found for award DOI: {award_doi}")
        return None

    description = None

    # When querying an award DOI there should only be one record
    raw_description = data[0].get("description")
    if isinstance(raw_description, str):
        description = clean_text(raw_description)
    else:
        append_error(errors, "No description found in OSTI Award record")

    if not description:
        append_error(errors, "OSTI Award record contained no description")
        return None

    return ResolverContext(
        text=description,
        raw_text=raw_description if isinstance(raw_description, str) else None,
        kind="description",
        source="osti_award",
    )