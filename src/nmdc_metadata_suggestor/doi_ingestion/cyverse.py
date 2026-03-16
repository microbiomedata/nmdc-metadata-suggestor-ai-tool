"""CyVerse DOI resolver."""

import logging
from urllib.parse import urljoin, urlparse

import requests

from nmdc_metadata_suggestor.constants import (
    CYVERSE_DATACOMMONS_API,
    CYVERSE_DATACOMMONS_BROWSE_HOST,
    CYVERSE_METADATA_API,
    CYVERSE_METADATA_SEARCH_API,
    DEFAULT_TIMEOUT,
    DOI_RESOLVER_URL,
    USER_AGENT,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    extract_doi_references,
    extract_http_urls,
    looks_like_document_url,
    merge_unique_strings,
    request_with_retry,
)
from nmdc_metadata_suggestor.models.resolver_context import ResolverContext

LOGGER = logging.getLogger(__name__)


def try_cyverse(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return cleaned/raw context from CyVerse Terrain or Data Commons metadata."""
    terrain_errors: list[str] = []
    datacommons_errors: list[str] = []
    accumulated_urls: list[str] | None = None
    accumulated_dois: list[str] | None = None

    metadata_avus = _search_cyverse_metadata_for_doi(
        doi, errors=terrain_errors)
    if metadata_avus:
        context = _extract_cyverse_context(metadata_avus, doi)
        if context is not None:
            accumulated_urls = merge_unique_strings(
                accumulated_urls, context.urls)
            accumulated_dois = merge_unique_strings(
                accumulated_dois, context.publication_dois)
            if context.text:
                return ResolverContext(
                    text=context.text,
                    raw_text=context.raw_text,
                    kind=context.kind,
                    source=context.source,
                    urls=accumulated_urls,
                    publication_dois=accumulated_dois,
                )

        for target_id in _extract_cyverse_target_ids(metadata_avus):
            target_avus = _fetch_cyverse_target_metadata(
                target_id, errors=terrain_errors)
            if not target_avus:
                continue
            context = _extract_cyverse_context(target_avus, doi)
            if context is not None:
                accumulated_urls = merge_unique_strings(
                    accumulated_urls, context.urls)
                accumulated_dois = merge_unique_strings(
                    accumulated_dois, context.publication_dois)
                if context.text:
                    return ResolverContext(
                        text=context.text,
                        raw_text=context.raw_text,
                        kind=context.kind,
                        source=context.source,
                        urls=accumulated_urls,
                        publication_dois=accumulated_dois,
                    )
    else:
        append_error(terrain_errors,
                     "CyVerse metadata search returned no AVUs")

    datacommons_context = _fetch_cyverse_datacommons_context(
        doi, errors=datacommons_errors)
    if datacommons_context is not None:
        if terrain_errors:
            LOGGER.info(
                "CyVerse resolved DOI %s through Data Commons after Terrain miss: %s",
                doi,
                "; ".join(terrain_errors),
            )
        return ResolverContext(
            text=datacommons_context.text,
            raw_text=datacommons_context.raw_text,
            kind=datacommons_context.kind,
            source=datacommons_context.source,
            urls=merge_unique_strings(
                accumulated_urls, datacommons_context.urls),
            publication_dois=merge_unique_strings(
                accumulated_dois,
                datacommons_context.publication_dois,
            ),
        )

    if accumulated_urls or accumulated_dois:
        return ResolverContext(
            text=None,
            raw_text=None,
            kind="description",
            urls=accumulated_urls,
            publication_dois=accumulated_dois,
        )

    _append_errors(errors, terrain_errors)
    _append_errors(errors, datacommons_errors)
    append_error(
        errors, "CyVerse metadata contained no usable abstract/description")
    return None


def _append_errors(errors: list[str] | None, new_errors: list[str]) -> None:
    """Append unique local errors into the shared resolver error collector."""
    for message in new_errors:
        append_error(errors, message)


def _search_cyverse_metadata_for_doi(
    doi: str, errors: list[str] | None = None
) -> list[dict[str, object]]:
    """Search CyVerse metadata AVUs for DOI references."""
    try:
        response = request_with_retry(
            "POST",
            CYVERSE_METADATA_SEARCH_API,
            json={"attribute": ["dc.identifier.doi"], "value": [doi]},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(
                errors, f"CyVerse metadata search returned HTTP {response.status_code}")
            return []
        payload = response.json()
    except requests.RequestException as exc:
        append_error(
            errors, f"CyVerse metadata search failed: {exc.__class__.__name__}")
        return []
    except ValueError:
        append_error(errors, "CyVerse metadata search returned invalid JSON")
        return []

    return _extract_cyverse_avu_list(payload)


def _fetch_cyverse_datacommons_context(
    doi: str, errors: list[str] | None = None
) -> ResolverContext | None:
    """Resolve a CyVerse DOI through the public Data Commons metadata APIs."""
    datacommons_path = _resolve_cyverse_datacommons_path(doi, errors=errors)
    if datacommons_path is None:
        return None

    item_id = _fetch_cyverse_datacommons_item_id(
        datacommons_path, errors=errors)
    if item_id is None:
        return None

    metadata_payload = _fetch_cyverse_datacommons_metadata(
        item_id, errors=errors)
    if metadata_payload is None:
        return None

    metadata_avus = _extract_cyverse_datacommons_avus(metadata_payload)
    if not metadata_avus:
        append_error(
            errors, "CyVerse Data Commons metadata returned no usable fields")
        return None

    return _extract_cyverse_context(metadata_avus, doi)


def _resolve_cyverse_datacommons_path(doi: str, errors: list[str] | None = None) -> str | None:
    """Resolve DOI redirect target and return the CyVerse Data Commons browse path."""
    try:
        response = request_with_retry(
            "GET",
            f"{DOI_RESOLVER_URL}/{doi}",
            allow_redirects=False,
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        append_error(
            errors, f"CyVerse DOI resolution failed: {exc.__class__.__name__}")
        return None

    if response.status_code not in {301, 302, 303, 307, 308}:
        append_error(
            errors, f"CyVerse DOI resolution returned HTTP {response.status_code}")
        return None

    location = response.headers.get("Location")
    if not isinstance(location, str) or not location.strip():
        append_error(
            errors, "CyVerse DOI resolution returned no redirect location")
        return None

    landing_url = urljoin(f"{DOI_RESOLVER_URL}/{doi}", location)
    parsed = urlparse(landing_url)
    if parsed.netloc != CYVERSE_DATACOMMONS_BROWSE_HOST or not parsed.path.startswith("/browse/"):
        append_error(
            errors, "CyVerse DOI did not resolve to a Data Commons browse URL")
        return None

    datacommons_path = parsed.path.removeprefix("/browse")
    if not datacommons_path:
        append_error(
            errors, "CyVerse DOI resolved to an empty Data Commons path")
        return None
    return datacommons_path


def _fetch_cyverse_datacommons_item_id(
    datacommons_path: str, errors: list[str] | None = None
) -> str | None:
    """Return the Data Commons item id for a browse path."""
    try:
        response = request_with_retry(
            "GET",
            CYVERSE_DATACOMMONS_API,
            params={
                "djng_url_name": "api_stat",
                "djng_url_kwarg_path": datacommons_path,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(
                errors,
                f"CyVerse Data Commons item lookup returned HTTP {response.status_code}",
            )
            return None
        payload = response.json()
    except requests.RequestException as exc:
        append_error(
            errors, f"CyVerse Data Commons item lookup failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(
            errors, "CyVerse Data Commons item lookup returned invalid JSON")
        return None

    item_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(item_id, str) or not item_id.strip():
        append_error(
            errors, "CyVerse Data Commons item lookup returned no item id")
        return None
    return item_id


def _fetch_cyverse_datacommons_metadata(
    item_id: str, errors: list[str] | None = None
) -> dict[str, object] | None:
    """Return structured Data Commons metadata for an item id."""
    try:
        response = request_with_retry(
            "GET",
            CYVERSE_DATACOMMONS_API,
            params={
                "djng_url_name": "api_metadata",
                "djng_url_kwarg_item_id": item_id,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(
                errors,
                f"CyVerse Data Commons metadata returned HTTP {response.status_code}",
            )
            return None
        payload = response.json()
    except requests.RequestException as exc:
        append_error(
            errors, f"CyVerse Data Commons metadata request failed: {exc.__class__.__name__}"
        )
        return None
    except ValueError:
        append_error(
            errors, "CyVerse Data Commons metadata returned invalid JSON")
        return None

    if not isinstance(payload, dict):
        append_error(
            errors, "CyVerse Data Commons metadata returned unexpected JSON")
        return None
    return payload


def _extract_cyverse_datacommons_avus(payload: dict[str, object]) -> list[dict[str, object]]:
    """Normalize Data Commons metadata JSON into AVU-like entries."""
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return []

    avus: list[dict[str, object]] = []
    for key, item in metadata.items():
        if not isinstance(item, dict):
            continue

        value = item.get("value")
        if not isinstance(value, str):
            continue

        attr = item.get("attr")
        if not isinstance(attr, str) or not attr.strip():
            attr = str(key)

        avus.append({"attr": attr, "value": value})
    return avus


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
            append_error(
                errors, f"CyVerse target metadata returned HTTP {response.status_code}")
            return []
        payload = response.json()
    except requests.RequestException as exc:
        append_error(
            errors, f"CyVerse target metadata request failed: {exc.__class__.__name__}")
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
) -> ResolverContext | None:
    """Choose best CyVerse metadata text from abstract/description-like AVUs."""
    best_rank = 99
    best_length = -1
    best_context: ResolverContext | None = None
    publication_urls, publication_dois = _extract_cyverse_publication_metadata(
        avus,
        requested_doi=requested_doi,
    )

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
            best_context = ResolverContext(
                text=cleaned,
                raw_text=raw_value,
                kind=kind,
                urls=publication_urls,
                publication_dois=publication_dois,
            )

    if best_context is not None:
        return best_context
    if publication_urls or publication_dois:
        return ResolverContext(
            text=None,
            raw_text=None,
            kind="description",
            urls=publication_urls,
            publication_dois=publication_dois,
        )
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


def _extract_cyverse_publication_metadata(
    avus: list[dict[str, object]], requested_doi: str
) -> tuple[list[str] | None, list[str] | None]:
    """Extract publication-like document URLs and DOIs from CyVerse AVUs."""
    publication_urls: list[str] | None = None
    publication_dois: list[str] | None = None

    for avu in _iter_cyverse_avus(avus):
        attr = avu.get("attr")
        value = avu.get("value")
        if not isinstance(attr, str) or not isinstance(value, str) or not value.strip():
            continue

        attr_lower = attr.strip().lower()
        extracted_urls = [url for url in extract_http_urls(
            value) if looks_like_document_url(url)]
        extracted_dois = extract_doi_references(
            value, requested_doi=requested_doi)

        if extracted_urls:
            publication_urls = merge_unique_strings(
                publication_urls, extracted_urls)

        if extracted_dois and (
            "publication" in attr_lower
            or "citation" in attr_lower
            or "manuscript" in attr_lower
            or "article" in attr_lower
            or "reference" in attr_lower
            or not _cyverse_value_is_requested_doi(value, requested_doi)
        ):
            publication_dois = merge_unique_strings(
                publication_dois, extracted_dois)

    return publication_urls, publication_dois
