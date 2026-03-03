"""Shared Crossref retrieval helpers for DOI ingestion and abstract retrieval."""

import requests

from nmdc_metadata_suggestor.constants import CROSSREF_API_URL, DEFAULT_TIMEOUT, USER_AGENT
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    request_with_retry,
    strip_jats_xml,
)
from nmdc_metadata_suggestor.models.resolver_context import ResolverContext


def try_crossref_context(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return Crossref abstract context and publisher metadata for DOI ingestion."""
    try:
        response = request_with_retry(
            "GET",
            f"{CROSSREF_API_URL}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"Crossref API returned HTTP {response.status_code}")
            return None
        message = response.json().get("message", {})
    except requests.RequestException as exc:
        append_error(errors, f"Crossref API request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(errors, "Crossref API returned invalid JSON")
        return None

    parsed = _extract_crossref_abstract(message)
    if parsed is None:
        append_error(errors, "Crossref response contained no abstract")
        return None

    cleaned, raw, fmt = parsed
    if not cleaned:
        append_error(errors, "Crossref abstract was empty after cleaning")
        return None

    publisher = message.get("publisher") if isinstance(message, dict) else None
    publisher_value = publisher if isinstance(publisher, str) else None
    return ResolverContext(text=cleaned, raw_text=raw, kind=fmt, source=publisher_value)


def try_crossref_abstract(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Fetch abstract from Crossref for publication abstract retrieval waterfall."""
    try:
        response = request_with_retry(
            "GET",
            f"{CROSSREF_API_URL}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"Crossref API returned HTTP {response.status_code}")
            return None
        message = response.json().get("message", {})
    except (requests.RequestException, ValueError):
        append_error(errors, "Crossref API request failed")
        return None

    parsed = _extract_crossref_abstract(message)
    if parsed is None:
        append_error(errors, "Crossref response contained no abstract")
        return None

    cleaned, raw, fmt = parsed
    return ResolverContext(text=cleaned, raw_text=raw, kind=fmt)


def _extract_crossref_abstract(message: object) -> tuple[str, str, str] | None:
    """Extract cleaned/raw Crossref abstract and format classification."""
    if not isinstance(message, dict):
        return None

    raw = message.get("abstract")
    if not isinstance(raw, str) or not raw.strip():
        return None

    cleaned = strip_jats_xml(raw)
    fmt = "jats_xml" if "<" in raw else "plain_text"
    return cleaned, raw, fmt
