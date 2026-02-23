"""Figshare DOI resolver."""

import requests

from nmdc_metadata_suggestor.doi_ingestion.common import append_error, clean_text
from nmdc_metadata_suggestor.publication_ingestion.doi_utils import (
    DEFAULT_TIMEOUT,
    USER_AGENT,
    request_with_retry,
)

FIGSHARE_API = "https://api.figshare.com/v2/articles"
FIGSHARE_COLLECTIONS_API = "https://api.figshare.com/v2/collections"


def try_figshare(doi: str, errors: list[str] | None = None) -> str | None:
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
) -> str | None:
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
) -> str | None:
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
            context = _extract_figshare_text(detail)
            if context:
                return context

        context = _extract_figshare_text(candidate)
        if context:
            return context
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
