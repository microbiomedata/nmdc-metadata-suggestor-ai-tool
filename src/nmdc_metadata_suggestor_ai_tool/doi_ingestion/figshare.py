"""Figshare DOI resolver."""

import requests

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_TIMEOUT,
    FIGSHARE_API,
    FIGSHARE_COLLECTIONS_API,
    USER_AGENT,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    request_with_retry,
)
from nmdc_metadata_suggestor_ai_tool.models.resolver_context import ResolverContext

# A Figshare DOI may name an article or a collection, and the two live behind
# separate endpoints with no way to tell them apart from the DOI alone. Both the
# resolver and the supplement retriever try them in this order.
FIGSHARE_ENDPOINTS = (FIGSHARE_API, FIGSHARE_COLLECTIONS_API)


def try_figshare(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return Figshare context text for article/collection DOI if present."""
    for endpoint in FIGSHARE_ENDPOINTS:
        payload = figshare_search(doi, endpoint, errors=errors)
        if payload is None:
            continue
        context = _extract_figshare_context_with_detail(payload, endpoint, errors=errors)
        if context:
            return context
    append_error(errors, "Figshare APIs returned no usable description")
    return None


def figshare_search(doi: str, endpoint: str, errors: list[str] | None = None) -> object | None:
    """Return the parsed payload of a Figshare DOI search against *endpoint*.

    Shared with the supplement retriever, which selects a record from the same
    search results. Returns ``None`` when the endpoint could not be queried.
    """
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
        return response.json()
    except requests.RequestException as exc:
        append_error(
            errors, f"Figshare endpoint {endpoint} request failed: {exc.__class__.__name__}"
        )
        return None
    except ValueError:
        append_error(errors, f"Figshare endpoint {endpoint} returned invalid JSON")
        return None


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
        detail = figshare_detail(candidate, endpoint, errors=errors)
        if detail is not None:
            context = _extract_figshare_text(detail)
            if context:
                return ResolverContext(text=context, raw_text=context, kind="description")

        context = _extract_figshare_text(candidate)
        if context:
            return ResolverContext(text=context, raw_text=context, kind="description")
    return None


def figshare_detail(
    candidate: dict[str, object], endpoint: str, errors: list[str] | None = None
) -> dict[str, object] | None:
    """Fetch the detailed Figshare record behind a summary-only search result.

    Shared with the supplement retriever, which needs the same detail record for
    its ``files`` list. Returns ``None`` when the record could not be fetched.
    """
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
