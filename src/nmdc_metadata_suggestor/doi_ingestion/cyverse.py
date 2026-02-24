"""CyVerse DOI resolver."""

import requests

from nmdc_metadata_suggestor.constants import (
    CYVERSE_METADATA_API,
    CYVERSE_METADATA_SEARCH_API,
    DEFAULT_TIMEOUT,
    USER_AGENT,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    request_with_retry,
)


def try_cyverse(doi: str, errors: list[str] | None = None) -> tuple[str, str, str] | None:
    """Return cleaned/raw context from CyVerse Terrain metadata."""
    metadata_avus = _search_cyverse_metadata_for_doi(doi, errors=errors)
    if not metadata_avus:
        append_error(errors, "CyVerse metadata search returned no AVUs")
        return None

    context = _extract_cyverse_context(metadata_avus, doi)
    if context is not None:
        return context

    for target_id in _extract_cyverse_target_ids(metadata_avus):
        target_avus = _fetch_cyverse_target_metadata(target_id, errors=errors)
        if not target_avus:
            continue
        context = _extract_cyverse_context(target_avus, doi)
        if context is not None:
            return context
    append_error(errors, "CyVerse metadata contained no usable abstract/description")
    return None


def _search_cyverse_metadata_for_doi(
    doi: str, errors: list[str] | None = None
) -> list[dict[str, object]]:
    """Search CyVerse metadata AVUs for DOI references."""
    try:
        response = request_with_retry(
            "POST",
            CYVERSE_METADATA_SEARCH_API,
            json={"attribute": "dc.identifier.doi", "value": doi, "avus": []},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"CyVerse metadata search returned HTTP {response.status_code}")
            return []
        payload = response.json()
    except requests.RequestException as exc:
        append_error(errors, f"CyVerse metadata search failed: {exc.__class__.__name__}")
        return []
    except ValueError:
        append_error(errors, "CyVerse metadata search returned invalid JSON")
        return []

    return _extract_cyverse_avu_list(payload)


def _fetch_cyverse_target_metadata(
    target_id: str, errors: list[str] | None = None
) -> list[dict[str, object]]:
    """Fetch AVUs for a specific CyVerse metadata target id."""
    try:
        response = request_with_retry(
            "GET",
            CYVERSE_METADATA_API,
            params={"target-id": target_id},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"CyVerse target metadata returned HTTP {response.status_code}")
            return []
        payload = response.json()
    except requests.RequestException as exc:
        append_error(errors, f"CyVerse target metadata request failed: {exc.__class__.__name__}")
        return []
    except ValueError:
        append_error(errors, "CyVerse target metadata returned invalid JSON")
        return []

    return _extract_cyverse_avu_list(payload)


def _extract_cyverse_avu_list(payload: object) -> list[dict[str, object]]:
    """Normalize CyVerse metadata payload to a flat AVU list."""
    if isinstance(payload, dict):
        avus = payload.get("avus")
        if isinstance(avus, list):
            return [item for item in avus if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _extract_cyverse_target_ids(avus: list[dict[str, object]]) -> list[str]:
    """Extract unique target IDs from CyVerse AVUs."""
    target_ids: list[str] = []
    seen: set[str] = set()
    for avu in _iter_cyverse_avus(avus):
        target_id = avu.get("target_id")
        if not isinstance(target_id, str) or not target_id.strip():
            continue
        if target_id in seen:
            continue
        seen.add(target_id)
        target_ids.append(target_id)
    return target_ids


def _extract_cyverse_context(
    avus: list[dict[str, object]], requested_doi: str
) -> tuple[str, str, str] | None:
    """Choose best CyVerse metadata text from abstract/description-like AVUs."""
    best_rank = 99
    best_length = -1
    best_context: tuple[str, str, str] | None = None

    for avu in _iter_cyverse_avus(avus):
        attr = avu.get("attr")
        raw_value = avu.get("value")
        if not isinstance(attr, str) or not isinstance(raw_value, str):
            continue

        cleaned = clean_text(raw_value)
        if not cleaned or _cyverse_value_is_requested_doi(cleaned, requested_doi):
            continue

        ranked = _rank_cyverse_context_attr(attr)
        if ranked is None:
            continue
        rank, kind = ranked

        if rank < best_rank or (rank == best_rank and len(cleaned) > best_length):
            best_rank = rank
            best_length = len(cleaned)
            best_context = cleaned, raw_value, kind

    return best_context


def _iter_cyverse_avus(avus: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return AVUs and nested AVUs as a flattened list."""
    flattened: list[dict[str, object]] = []
    for avu in avus:
        flattened.append(avu)
        nested = avu.get("avus")
        if not isinstance(nested, list):
            continue
        nested_avus = [item for item in nested if isinstance(item, dict)]
        if nested_avus:
            flattened.extend(_iter_cyverse_avus(nested_avus))
    return flattened


def _rank_cyverse_context_attr(attr: str) -> tuple[int, str] | None:
    """Map CyVerse AVU attribute names to context extraction priority."""
    lowered = attr.strip().lower()
    if not lowered:
        return None

    if "abstract" in lowered:
        return 0, "abstract"
    if "description" in lowered:
        return 1, "description"
    if "summary" in lowered:
        return 2, "description"
    if "title" in lowered:
        return 3, "description"
    if "note" in lowered or "comment" in lowered:
        return 4, "description"
    return None


def _cyverse_value_is_requested_doi(value: str, requested_doi: str) -> bool:
    """Return True when a CyVerse metadata value is just the DOI identifier."""
    lowered = value.strip().lower()
    doi_value = requested_doi.lower()
    return lowered in {
        doi_value,
        f"doi:{doi_value}",
        f"https://doi.org/{doi_value}",
        f"http://doi.org/{doi_value}",
    }
