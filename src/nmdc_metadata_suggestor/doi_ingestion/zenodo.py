"""Zenodo DOI resolver."""

import requests

from nmdc_metadata_suggestor.constants import DEFAULT_TIMEOUT, USER_AGENT, ZENODO_API
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    normalize_doi,
    request_with_retry,
)
from nmdc_metadata_suggestor.doi_ingestion.resolver_context import ResolverContext


def try_zenodo(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return Zenodo description text for a DOI if present."""
    query = f'doi:"{doi}" OR conceptdoi:"{doi}"'
    try:
        response = request_with_retry(
            "GET",
            ZENODO_API,
            params={"q": query},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"Zenodo API returned HTTP {response.status_code}")
            return None
        payload = response.json()
    except requests.RequestException as exc:
        append_error(errors, f"Zenodo API request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(errors, "Zenodo API returned invalid JSON")
        return None

    hits = payload.get("hits", {}).get("hits", [])
    if not isinstance(hits, list):
        append_error(errors, "Zenodo response missing hits list")
        return None

    matching_hits = [hit for hit in hits if _zenodo_hit_matches_doi(hit, doi)]
    if not matching_hits:
        matching_hits = hits

    for hit in matching_hits:
        if not isinstance(hit, dict):
            continue
        metadata = hit.get("metadata", {})
        if not isinstance(metadata, dict):
            continue
        description = metadata.get("description")
        if isinstance(description, str):
            cleaned = clean_text(description)
            if cleaned:
                return ResolverContext(text=cleaned, raw_text=description, kind="description")
    append_error(errors, "Zenodo response contained no usable description")
    return None


def _zenodo_hit_matches_doi(hit: object, requested_doi: str) -> bool:
    """Return True when a Zenodo hit DOI or concept DOI matches the request."""
    if not isinstance(hit, dict):
        return False

    candidates: list[str] = []
    for key in ("doi", "conceptdoi"):
        value = hit.get(key)
        if isinstance(value, str):
            candidates.append(value)

    metadata = hit.get("metadata")
    if isinstance(metadata, dict):
        metadata_doi = metadata.get("doi")
        if isinstance(metadata_doi, str):
            candidates.append(metadata_doi)

    requested = requested_doi.lower()
    for candidate in candidates:
        normalized = normalize_doi(candidate).lower()
        if normalized == requested:
            return True
    return False
