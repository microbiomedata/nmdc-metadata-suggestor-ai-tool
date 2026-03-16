"""EMSL DOI resolver."""

import re

import requests

from nmdc_metadata_suggestor.constants import DEFAULT_TIMEOUT, EMSL_PROJECTS_API, USER_AGENT
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    extract_document_urls_from_file_entries,
    extract_doi_references,
    extract_related_publication_dois,
    merge_unique_strings,
    normalize_doi,
    request_with_retry,
)
from nmdc_metadata_suggestor.models.resolver_context import ResolverContext


def try_emsl(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return cleaned/raw context from EMSL project details resolved by DOI."""
    project_id = _extract_emsl_project_id(doi)
    if project_id is None:
        append_error(errors, "Could not extract EMSL project id from DOI")
        return None

    try:
        response = request_with_retry(
            "GET",
            f"{EMSL_PROJECTS_API}/{project_id}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(
                errors, f"EMSL API returned HTTP {response.status_code}")
            return None
        payload = response.json()
    except requests.RequestException as exc:
        append_error(
            errors, f"EMSL API request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(errors, "EMSL API returned invalid JSON")
        return None

    award_doi = payload.get("award_doi")
    if isinstance(award_doi, str) and award_doi:
        if normalize_doi(award_doi) != doi:
            append_error(errors, f"EMSL award_doi mismatch: {award_doi}")
            return None

    publication_urls, publication_dois = _extract_emsl_publication_metadata(
        payload,
        requested_doi=doi,
    )

    abstract = payload.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        cleaned = clean_text(abstract)
        if cleaned:
            return ResolverContext(
                text=cleaned,
                raw_text=abstract,
                kind="abstract",
                urls=publication_urls,
                publication_dois=publication_dois,
            )

    for key in ("title", "project_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = clean_text(value)
            if cleaned:
                return ResolverContext(
                    text=cleaned,
                    raw_text=value,
                    kind="description",
                    urls=publication_urls,
                    publication_dois=publication_dois,
                )
    if publication_urls or publication_dois:
        return ResolverContext(
            text=None,
            raw_text=None,
            kind="description",
            urls=publication_urls,
            publication_dois=publication_dois,
        )
    append_error(errors, "EMSL API contained no abstract/description fields")
    return None


def _extract_emsl_project_id(doi: str) -> str | None:
    """Extract EMSL project ID embedded in award DOI suffix."""
    # Example: 10.46936/jejc.proj.2014.48483/60005501 -> 48483
    match = re.search(r"\.proj\.\d{4}\.(\d+)(?:/|$)", doi)
    if match is None:
        return None
    return match.group(1)


def _extract_emsl_publication_metadata(
    payload: dict[str, object], requested_doi: str
) -> tuple[list[str] | None, list[str] | None]:
    """Extract publication file links and related publication DOIs from EMSL payloads."""
    publication_urls: list[str] | None = None
    publication_dois: list[str] | None = None

    for key in ("files", "documents", "publications", "related_publications"):
        publication_urls = merge_unique_strings(
            publication_urls,
            extract_document_urls_from_file_entries(payload.get(key)),
        )
        publication_dois = merge_unique_strings(
            publication_dois,
            extract_related_publication_dois(
                payload.get(key), requested_doi=requested_doi),
        )

    for key in ("publication_url", "fulltext_url", "pdf_url"):
        value = payload.get(key)
        if isinstance(value, str):
            publication_urls = merge_unique_strings(
                publication_urls,
                extract_document_urls_from_file_entries([{"url": value}]),
            )

    for key in ("publication_doi", "publication_dois"):
        value = payload.get(key)
        if isinstance(value, str):
            publication_dois = merge_unique_strings(
                publication_dois,
                extract_doi_references(value, requested_doi=requested_doi),
            )
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    publication_dois = merge_unique_strings(
                        publication_dois,
                        extract_doi_references(
                            item, requested_doi=requested_doi),
                    )

    return publication_urls, publication_dois
