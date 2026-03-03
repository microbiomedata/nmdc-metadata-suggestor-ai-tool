"""Elsevier ScienceDirect abstract retrieval for 10.1016 DOIs.

Elsevier withholds abstracts from Crossref, so the standard waterfall
(OpenAlex -> Crossref -> PubMed -> content negotiation) fails for most
10.1016 DOIs. This module queries the ScienceDirect API directly.

API docs: https://dev.elsevier.com/documentation/AbstractRetrievalAPI.doc
"""

import logging
import os

import requests

from nmdc_metadata_suggestor.constants import (
    DEFAULT_TIMEOUT,
    ELSEVIER_API_URL,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    request_with_retry,
)
from nmdc_metadata_suggestor.doi_ingestion.resolver_context import ResolverContext

LOGGER = logging.getLogger(__name__)

ELSEVIER_DOI_PREFIX = "10.1016/"


def try_elsevier(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return Elsevier abstract context for DOI ingestion waterfall.

    Compatible with the ``doi_ingestion/main.py`` resolver interface.
    """
    text, raw = _fetch_elsevier_abstract(doi, errors)
    if text:
        return ResolverContext(text=text, raw_text=raw or "", kind="abstract", source="elsevier")
    return None


def try_elsevier_abstract(doi: str) -> tuple[str | None, str | None, str | None]:
    """Fetch abstract from Elsevier for publication abstract retrieval waterfall.

    Matches the ``try_crossref_abstract`` signature:
    returns ``(cleaned_text, raw_text, content_format)`` tuple.
    """
    text, raw = _fetch_elsevier_abstract(doi)
    if text:
        return text, raw, "plain_text"
    return None, None, None


def _fetch_elsevier_abstract(
    doi: str, errors: list[str] | None = None
) -> tuple[str | None, str | None]:
    """Shared internal logic for both public entry points.

    Returns ``(cleaned_text, raw_text)`` or ``(None, None)`` on failure.
    """
    api_key = os.environ.get("ELSEVIER_API_KEY")
    if not api_key:
        LOGGER.debug("Elsevier API key not configured, skipping")
        return None, None

    if not doi.startswith(ELSEVIER_DOI_PREFIX):
        return None, None

    try:
        response = request_with_retry(
            "GET",
            f"{ELSEVIER_API_URL}/{doi}",
            headers={
                "X-ELS-APIKey": api_key,
                "Accept": "application/json",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        _log_rate_limit(response)
        if response.status_code != 200:
            append_error(errors, f"Elsevier API returned HTTP {response.status_code}")
            return None, None

        data = response.json()
    except requests.RequestException as exc:
        append_error(errors, f"Elsevier API request failed: {exc.__class__.__name__}")
        return None, None
    except ValueError:
        append_error(errors, "Elsevier API returned invalid JSON")
        return None, None

    coredata = data.get("full-text-retrieval-response", {}).get("coredata", {})
    raw = coredata.get("dc:description")
    if not isinstance(raw, str) or not raw.strip():
        append_error(errors, "Elsevier response contained no dc:description")
        return None, None

    cleaned = clean_text(raw)
    if not cleaned:
        append_error(errors, "Elsevier abstract was empty after cleaning")
        return None, None

    return cleaned, raw


def _log_rate_limit(response: requests.Response) -> None:
    """Log Elsevier API rate-limit headers for quota monitoring."""
    remaining = response.headers.get("X-RateLimit-Remaining")
    limit = response.headers.get("X-RateLimit-Limit")
    if remaining is not None:
        LOGGER.info("Elsevier API quota: %s/%s remaining", remaining, limit or "?")
        try:
            if int(remaining) < 100:
                LOGGER.warning("Elsevier API quota low: %s remaining", remaining)
        except ValueError:
            pass
