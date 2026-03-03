"""Springer Nature Metadata API abstract retrieval for 10.1007 and 10.1038 DOIs.

Springer Nature provides abstracts via a free Metadata API. Many Springer and
Nature DOIs have abstracts available through OpenAlex, but this module serves
as a dedicated fallback when upstream sources miss.

API docs: https://dev.springernature.com/docs/api-endpoints/metadata-api
"""

import logging
import os

import requests

from nmdc_metadata_suggestor.constants import (
    DEFAULT_TIMEOUT,
    SPRINGER_NATURE_API_URL,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    request_with_retry,
)
from nmdc_metadata_suggestor.doi_ingestion.resolver_context import ResolverContext

LOGGER = logging.getLogger(__name__)

SPRINGER_NATURE_DOI_PREFIXES = ("10.1007/", "10.1038/")


def try_springer_nature(
    doi: str, errors: list[str] | None = None
) -> ResolverContext | None:
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

    Returns ``(cleaned_text, raw_text)`` or ``(None, None)`` on failure.
    """
    api_key = os.environ.get("SPRINGER_NATURE_API_KEY")
    if not api_key:
        LOGGER.debug("Springer Nature API key not configured, skipping")
        return None, None

    if not any(doi.startswith(prefix) for prefix in SPRINGER_NATURE_DOI_PREFIXES):
        return None, None

    try:
        response = request_with_retry(
            "GET",
            SPRINGER_NATURE_API_URL,
            params={
                "api_key": api_key,
                "q": f"doi:{doi}",
                "p": "1",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        _log_rate_limit(response)
        if response.status_code != 200:
            append_error(errors, f"Springer Nature API returned HTTP {response.status_code}")
            return None, None

        data = response.json()
    except requests.RequestException as exc:
        append_error(errors, f"Springer Nature API request failed: {exc.__class__.__name__}")
        return None, None
    except ValueError:
        append_error(errors, "Springer Nature API returned invalid JSON")
        return None, None

    records = data.get("records", [])
    if not records:
        append_error(errors, "Springer Nature response contained no records")
        return None, None

    raw = records[0].get("abstract")
    if not isinstance(raw, str) or not raw.strip():
        append_error(errors, "Springer Nature record contained no abstract")
        return None, None

    cleaned = clean_text(raw)
    if not cleaned:
        append_error(errors, "Springer Nature abstract was empty after cleaning")
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
