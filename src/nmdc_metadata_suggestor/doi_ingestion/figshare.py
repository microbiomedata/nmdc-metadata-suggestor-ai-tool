"""Figshare DOI resolver."""

import requests

from nmdc_metadata_suggestor.constants import (
    DEFAULT_TIMEOUT,
    FIGSHARE_API,
    FIGSHARE_COLLECTIONS_API,
    USER_AGENT,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    extract_document_urls_from_file_entries,
    extract_doi_references,
    merge_unique_strings,
    request_with_retry,
)
from nmdc_metadata_suggestor.models.resolver_context import ResolverContext


def try_figshare(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return Figshare context text for article/collection DOI if present."""
    context = _try_figshare_entity_lookup(doi, FIGSHARE_API, errors=errors)
    if context:
        return context

    context = _try_figshare_entity_lookup(doi, FIGSHARE_COLLECTIONS_API, errors=errors)
    if context:
        return context
    append_error(errors, "Figshare APIs returned no usable description")
    return None


def _try_figshare_entity_lookup(
    doi: str, endpoint: str, errors: list[str] | None = None
) -> ResolverContext | None:
    """Lookup a Figshare entity list by DOI and extract context."""
    try:
        response = request_with_retry(
            "GET",
            endpoint,
            params={"doi": doi},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(
                errors, f"Figshare endpoint {endpoint} returned HTTP {response.status_code}"
            )
            return None
        payload = response.json()
    except requests.RequestException as exc:
        append_error(
            errors, f"Figshare endpoint {endpoint} request failed: {exc.__class__.__name__}"
        )
        return None
    except ValueError:
        append_error(errors, f"Figshare endpoint {endpoint} returned invalid JSON")
        return None
    return _extract_figshare_context_with_detail(payload, endpoint, errors=errors)


def _extract_figshare_context_with_detail(
    payload: object, endpoint: str, errors: list[str] | None = None
) -> ResolverContext | None:
    """Extract context from Figshare payload, preferring detailed records."""
    if isinstance(payload, dict):
        candidates: list[dict[str, object]] = [payload]
    elif isinstance(payload, list):
        candidates = [item for item in payload if isinstance(item, dict)]
    else:
        candidates = []

    for candidate in candidates:
        detail = _fetch_figshare_detail(candidate, endpoint, errors=errors)
        if detail is not None:
            publication_urls, publication_dois = _extract_figshare_publication_metadata(detail)
            context = _extract_figshare_text(detail)
            if context:
                return ResolverContext(
                    text=context,
                    raw_text=context,
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

        publication_urls, publication_dois = _extract_figshare_publication_metadata(candidate)
        context = _extract_figshare_text(candidate)
        if context:
            return ResolverContext(
                text=context,
                raw_text=context,
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
    return None


def _fetch_figshare_detail(
    candidate: dict[str, object], endpoint: str, errors: list[str] | None = None
) -> dict[str, object] | None:
    """Fetch a detailed Figshare record when search payload is summary-only."""
    detail_url = candidate.get("url_public_api")
    if not isinstance(detail_url, str) or not detail_url.strip():
        candidate_id = candidate.get("id")
        if isinstance(candidate_id, int) or (
            isinstance(candidate_id, str) and candidate_id.strip().isdigit()
        ):
            detail_url = f"{endpoint}/{candidate_id}"
        else:
            return None

    try:
        response = request_with_retry(
            "GET",
            detail_url,
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"Figshare detail request returned HTTP {response.status_code}")
            return None
        payload = response.json()
    except requests.RequestException as exc:
        append_error(errors, f"Figshare detail request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(errors, "Figshare detail request returned invalid JSON")
        return None

    if isinstance(payload, dict):
        return payload
    return None


def _extract_figshare_text(record: dict[str, object]) -> str | None:
    """Extract best available context text from a Figshare entity record."""
    for key in ("description", "resource_title", "title", "citation"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        cleaned = clean_text(value)
        if cleaned:
            return cleaned
    return None


def _extract_figshare_publication_metadata(
    record: dict[str, object],
) -> tuple[list[str] | None, list[str] | None]:
    """Extract direct document links and related publication DOIs from Figshare records."""
    publication_urls = extract_document_urls_from_file_entries(record.get("files"))

    publication_dois: list[str] | None = None
    resource_doi = record.get("resource_doi")
    if isinstance(resource_doi, str):
        publication_dois = merge_unique_strings(
            publication_dois,
            extract_doi_references(resource_doi),
        )

    references = record.get("references")
    if isinstance(references, list):
        for reference in references:
            if isinstance(reference, str):
                publication_dois = merge_unique_strings(
                    publication_dois,
                    extract_doi_references(reference),
                )

    return publication_urls, publication_dois
