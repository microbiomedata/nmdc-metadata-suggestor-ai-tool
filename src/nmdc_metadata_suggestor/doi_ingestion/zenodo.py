"""Zenodo DOI resolver."""

import requests

from nmdc_metadata_suggestor.doi_ingestion.common import clean_text
from nmdc_metadata_suggestor.publication_ingestion.doi_utils import (
    DEFAULT_TIMEOUT,
    USER_AGENT,
    normalize_doi,
)

ZENODO_API = "https://zenodo.org/api/records"


def try_zenodo(doi: str) -> str | None:
    """Return Zenodo description text for a DOI if present."""
    query = f'doi:"{doi}" OR conceptdoi:"{doi}"'
    try:
        response = requests.get(
            ZENODO_API,
            params={"q": query},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    hits = payload.get("hits", {}).get("hits", [])
    if not isinstance(hits, list):
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
                return cleaned
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
