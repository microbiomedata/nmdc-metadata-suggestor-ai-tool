"""Springer Nature API abstract retrieval for 10.1007 and 10.1038 DOIs.

Springer Nature provides abstracts via two free APIs, each requiring its own key
from https://dev.springernature.com/:

  - **Meta API v2** (``SPRINGER_NATURE_API_KEY``) — covers all Springer Nature
    content including paywalled articles.  Abstracts from non-OA articles are
    subject to Section 8.2 generative-AI restrictions.
  - **Open Access API** (``SPRINGER_NATURE_OA_API_KEY``) — OA content only,
    all CC-licensed with no AI restrictions.

The module tries Meta API first (broader coverage), then falls back to the
Open Access API.

API docs: https://dev.springernature.com/docs/api-endpoints/metadata-api
"""

import logging
import os

import requests

from nmdc_metadata_suggestor.constants import (
    DEFAULT_TIMEOUT,
    SPRINGER_NATURE_META_API_URL,
    SPRINGER_NATURE_OA_API_URL,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    request_with_retry,
)
from nmdc_metadata_suggestor.doi_ingestion.resolver_context import ResolverContext

LOGGER = logging.getLogger(__name__)

SPRINGER_NATURE_DOI_PREFIXES = ("10.1007/", "10.1038/")


def try_springer_nature(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return Springer Nature abstract context for DOI ingestion waterfall.

    Compatible with the ``doi_ingestion/main.py`` resolver interface.
    """
    text, raw = _fetch_springer_nature_abstract(doi, errors)
    if text:
        return ResolverContext(text=text, raw_text=raw, kind="abstract", source="springer_nature")
    return None


def try_springer_nature_abstract(doi: str) -> tuple[str | None, str | None, str | None]:
    """Fetch abstract from Springer Nature for publication abstract retrieval waterfall.

    Matches the ``try_crossref_abstract`` signature:
    returns ``(cleaned_text, raw_text, content_format)`` tuple.
    """
    text, raw = _fetch_springer_nature_abstract(doi)
    if text:
        return text, raw, "plain_text"
    return None, None, None


def _fetch_springer_nature_abstract(
    doi: str, errors: list[str] | None = None
) -> tuple[str | None, str | None]:
    """Shared internal logic for both public entry points.

    Tries the Meta API v2 first (broader coverage), then falls back to the
    Open Access API.  Each requires its own key.

    Returns ``(cleaned_text, raw_text)`` or ``(None, None)`` on failure.
    """
    if not any(doi.startswith(prefix) for prefix in SPRINGER_NATURE_DOI_PREFIXES):
        return None, None

    # Build list of (url, key, label) to try in order.
    endpoints: list[tuple[str, str, str]] = []
    meta_key = os.environ.get("SPRINGER_NATURE_API_KEY")
    oa_key = os.environ.get("SPRINGER_NATURE_OA_API_KEY")
    if meta_key:
        endpoints.append((SPRINGER_NATURE_META_API_URL, meta_key, "Meta API v2"))
    if oa_key:
        endpoints.append((SPRINGER_NATURE_OA_API_URL, oa_key, "Open Access API"))

    if not endpoints:
        LOGGER.debug("No Springer Nature API keys configured, skipping")
        return None, None

    for url, key, label in endpoints:
        result = _try_endpoint(doi, url, key, label, errors)
        if result != (None, None):
            return result

    return None, None


def _try_endpoint(
    doi: str,
    api_url: str,
    api_key: str,
    label: str,
    errors: list[str] | None = None,
) -> tuple[str | None, str | None]:
    """Try a single Springer Nature endpoint. Returns (cleaned, raw) or (None, None)."""
    try:
        response = request_with_retry(
            "GET",
            api_url,
            params={
                "api_key": api_key,
                "q": f"doi:{doi}",
                "p": "1",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        _log_rate_limit(response)
        if response.status_code != 200:
            append_error(errors, f"Springer Nature {label} returned HTTP {response.status_code}")
            return None, None

        data = response.json()
    except requests.RequestException as exc:
        append_error(errors, f"Springer Nature {label} request failed: {exc.__class__.__name__}")
        return None, None
    except ValueError:
        append_error(errors, f"Springer Nature {label} returned invalid JSON")
        return None, None

    records = data.get("records", [])
    if not records:
        return None, None

    raw = records[0].get("abstract")
    if not isinstance(raw, str) or not raw.strip():
        return None, None

    cleaned = clean_text(raw)
    if not cleaned:
        return None, None

    return cleaned, raw


def _log_rate_limit(response: requests.Response) -> None:
    """Log Springer Nature API rate-limit headers for quota monitoring."""
    remaining = response.headers.get("X-RateLimit-Remaining")
    limit = response.headers.get("X-RateLimit-Limit")
    if remaining is not None:
        LOGGER.info("Springer Nature API quota: %s/%s remaining", remaining, limit or "?")
        try:
            if int(remaining) < 100:
                LOGGER.warning("Springer Nature API quota low: %s remaining", remaining)
        except ValueError:
            pass
