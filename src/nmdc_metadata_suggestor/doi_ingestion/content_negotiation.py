"""Shared DOI content-negotiation helpers for context and abstract retrieval."""

import requests

from nmdc_metadata_suggestor.constants import (
    CITEPROC_JSON_ACCEPT,
    DEFAULT_TIMEOUT,
    DOI_CONTENT_NEGOTIATION_API,
    USER_AGENT,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    clean_text,
    request_with_retry,
    strip_jats_xml,
)
from nmdc_metadata_suggestor.doi_ingestion.resolver_context import ResolverContext


def try_content_negotiation_context(
    doi: str, errors: list[str] | None = None
) -> ResolverContext | None:
    """Return DOI content-negotiation context (abstract, then note fallback)."""
    payload = _fetch_content_negotiation_payload(doi, errors=errors)
    if payload is None:
        return None

    abstract = payload.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        cleaned = strip_jats_xml(abstract)
        if cleaned:
            return ResolverContext(text=cleaned, raw_text=abstract, kind="abstract")

    note = payload.get("note")
    if isinstance(note, str) and note.strip():
        cleaned = clean_text(note)
        if cleaned:
            return ResolverContext(text=cleaned, raw_text=note, kind="description")

    if errors is not None:
        errors.append("DOI content negotiation contained no abstract/note")
    return None


def try_content_negotiation_abstract(
    doi: str,
) -> tuple[str | None, str | None, str | None]:
    """Return abstract-only content negotiation result (intentionally no note fallback)."""
    payload = _fetch_content_negotiation_payload(doi)
    if payload is None:
        return None, None, None

    raw = payload.get("abstract")
    if not isinstance(raw, str) or not raw.strip():
        return None, None, None

    cleaned = strip_jats_xml(raw)
    if not cleaned:
        return None, None, None
    fmt = "jats_xml" if "<" in raw else "citeproc_json"
    return cleaned, raw, fmt


def _fetch_content_negotiation_payload(
    doi: str, errors: list[str] | None = None
) -> dict[str, object] | None:
    """Fetch and parse citeproc-json payload for a DOI."""
    try:
        response = request_with_retry(
            "GET",
            f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
            headers={
                "Accept": CITEPROC_JSON_ACCEPT,
                "User-Agent": USER_AGENT,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            if errors is not None:
                errors.append(
                    f"DOI content negotiation returned HTTP {response.status_code}")
            return None
        payload = response.json()
    except requests.RequestException as exc:
        if errors is not None:
            errors.append(
                f"DOI content negotiation request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        if errors is not None:
            errors.append("DOI content negotiation returned invalid JSON")
        return None

    if isinstance(payload, dict):
        return payload
    if errors is not None:
        errors.append("DOI content negotiation returned non-object JSON")
    return None
