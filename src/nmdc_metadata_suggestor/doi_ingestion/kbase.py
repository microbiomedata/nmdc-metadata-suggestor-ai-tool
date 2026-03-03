"""KBase DOI resolver."""

import re

import requests

from nmdc_metadata_suggestor.constants import (
    DEFAULT_TIMEOUT,
    KBASE_SEARCH_API,
    KBASE_WORKSPACE_API,
    USER_AGENT,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    request_with_retry,
    text_mentions_doi,
)
from nmdc_metadata_suggestor.models.resolver_context import ResolverContext


def try_kbase(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return cleaned/raw context from KBase narrative search and workspace refs."""
    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "search_objects",
        "params": {
            "query": {"query_string": {"query": f'"{doi}"'}},
            "indexes": ["narrative"],
            "source": ["narrative_title", "cells.desc", "creation_date"],
            "only_public": True,
            "size": 10,
            "from": 0,
            "sort": [{"creation_date": {"order": "desc"}}],
            "track_total_hits": False,
        },
    }

    try:
        response = request_with_retry(
            "POST",
            KBASE_SEARCH_API,
            json=payload,
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"KBase search API returned HTTP {response.status_code}")
            return None
        data = response.json()
    except requests.RequestException as exc:
        append_error(errors, f"KBase search API request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(errors, "KBase search API returned invalid JSON")
        return None

    result = data.get("result")
    if not isinstance(result, dict):
        append_error(errors, "KBase search API response missing result object")
        return None

    context = _extract_kbase_context(result, doi)
    if context is not None:
        return context

    workspace_context = _try_kbase_workspace_ref(doi, errors=errors)
    if workspace_context is None:
        append_error(errors, "KBase APIs returned no matching abstract/description")
    return workspace_context


def _extract_kbase_context(result: dict[str, object], requested_doi: str) -> ResolverContext | None:
    """Extract description context from KBase narrative search results."""
    hits = result.get("hits")
    if not isinstance(hits, list):
        return None

    best_matching_text: str | None = None
    best_fallback_text: str | None = None
    best_title: str | None = None

    for hit in hits:
        if not isinstance(hit, dict):
            continue
        doc = hit.get("doc")
        if not isinstance(doc, dict):
            continue

        title = doc.get("narrative_title")
        if best_title is None and isinstance(title, str) and title.strip():
            best_title = title

        cells = doc.get("cells")
        if not isinstance(cells, list):
            continue

        for cell in cells:
            if not isinstance(cell, dict):
                continue
            desc = cell.get("desc")
            if not isinstance(desc, str) or not desc.strip():
                continue

            if text_mentions_doi(desc, requested_doi):
                if best_matching_text is None or len(desc) > len(best_matching_text):
                    best_matching_text = desc
            elif best_fallback_text is None or len(desc) > len(best_fallback_text):
                best_fallback_text = desc

    if best_matching_text:
        cleaned = clean_text(best_matching_text)
        if cleaned:
            return ResolverContext(text=cleaned, raw_text=best_matching_text, kind="description")

    if best_fallback_text:
        cleaned = clean_text(best_fallback_text)
        if cleaned:
            return ResolverContext(text=cleaned, raw_text=best_fallback_text, kind="description")

    if best_title:
        cleaned = clean_text(best_title)
        if cleaned:
            return ResolverContext(text=cleaned, raw_text=best_title, kind="description")
    return None


def _try_kbase_workspace_ref(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return context from KBase workspace object info inferred from DOI token."""
    workspace_tokens = _extract_kbase_workspace_tokens(doi)
    if workspace_tokens is None:
        append_error(errors, "Could not extract KBase workspace token from DOI")
        return None
    wsid, token = workspace_tokens

    candidate_refs = [
        f"{wsid}/1/{token}",
        f"{wsid}/{token}",
        f"{wsid}/{token}/1",
    ]

    seen: set[str] = set()
    for ref in candidate_refs:
        if ref in seen:
            continue
        seen.add(ref)
        info = _fetch_kbase_object_info(ref, errors=errors)
        if info is None:
            continue
        context = _extract_kbase_object_info_context(info)
        if context is not None:
            return context
    return None


def _extract_kbase_workspace_tokens(doi: str) -> tuple[str, str] | None:
    """Extract KBase workspace ID and object/version token from DOI."""
    match = re.match(r"^10\.25982/(\d+)\.(\d+)(?:/|$)", doi)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _fetch_kbase_object_info(ref: str, errors: list[str] | None = None) -> list[object] | None:
    """Fetch KBase workspace object info tuple by object reference."""
    payload = {
        "version": "1.1",
        "id": "1",
        "method": "Workspace.get_object_info3",
        "params": [{"objects": [{"ref": ref}], "includeMetadata": 1}],
    }
    try:
        response = request_with_retry(
            "POST",
            KBASE_WORKSPACE_API,
            json=payload,
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"KBase workspace API returned HTTP {response.status_code}")
            return None
        data = response.json()
    except requests.RequestException as exc:
        append_error(errors, f"KBase workspace API request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(errors, "KBase workspace API returned invalid JSON")
        return None

    if not isinstance(data, dict):
        append_error(errors, "KBase workspace API response not an object")
        return None
    if "error" in data:
        append_error(errors, f"KBase workspace API error for ref {ref}")
        return None

    result = data.get("result")
    if not isinstance(result, list) or not result:
        return None

    first_result = result[0]
    if not isinstance(first_result, dict):
        return None
    infos = first_result.get("infos")
    if not isinstance(infos, list) or not infos:
        return None
    info = infos[0]
    if not isinstance(info, list):
        return None
    return info


def _extract_kbase_object_info_context(info: list[object]) -> ResolverContext | None:
    """Extract description context from a KBase workspace object info tuple."""
    object_name = info[1] if len(info) > 1 else None
    object_type = info[2] if len(info) > 2 else None
    metadata = info[10] if len(info) > 10 else None

    if not isinstance(metadata, dict):
        metadata = {}

    candidates: list[str] = []
    for key in ("description", "name", "title", "narrative_nice_name"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value)

    if isinstance(object_name, str) and object_name.strip():
        candidates.append(object_name)
    if isinstance(object_type, str) and object_type.startswith("KBaseNarrative.Narrative"):
        ws_name = metadata.get("ws_name")
        if isinstance(ws_name, str) and ws_name.strip():
            candidates.append(ws_name)

    for raw in candidates:
        cleaned = clean_text(raw)
        if not cleaned:
            continue
        if _is_uninformative_kbase_text(cleaned):
            continue
        return ResolverContext(text=cleaned, raw_text=raw, kind="description")

    summary = _build_kbase_object_metadata_summary(object_name, object_type, metadata)
    if summary is not None:
        cleaned = clean_text(summary)
        if cleaned:
            return ResolverContext(text=cleaned, raw_text=summary, kind="description")
    return None


def _is_uninformative_kbase_text(text: str) -> bool:
    """Return True for technical KBase labels that are weak LLM context."""
    lowered = text.lower().strip()
    if not lowered:
        return True
    if lowered.startswith("narrative."):
        return True
    if "__" in text and " " not in text:
        return True
    return False


def _build_kbase_object_metadata_summary(
    object_name: object, object_type: object, metadata: dict[str, object]
) -> str | None:
    """Build short context text from non-narrative KBase object metadata."""
    if isinstance(object_type, str) and object_type.startswith("KBaseNarrative.Narrative"):
        return None

    summary_parts: list[str] = []
    for key in ("Size", "N Contigs", "GC content"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            summary_parts.append(f"{key}={value.strip()}")

    if not summary_parts:
        return None

    label = _humanize_kbase_object_name(object_name)
    type_text = (
        object_type.strip() if isinstance(object_type, str) and object_type.strip() else None
    )

    context_parts: list[str] = []
    context_parts.append(label if label else "KBase object")
    if type_text:
        context_parts.append(f"type {type_text}")
    context_parts.append(f"metadata: {', '.join(summary_parts)}")
    return " ; ".join(context_parts)


def _humanize_kbase_object_name(object_name: object) -> str | None:
    """Convert KBase object names with separators into readable labels."""
    if not isinstance(object_name, str):
        return None
    stripped = object_name.strip()
    if not stripped:
        return None
    if stripped.lower().startswith("narrative."):
        return None

    humanized = re.sub(r"[_\-.]+", " ", stripped)
    humanized = re.sub(r"\s+", " ", humanized).strip()
    return humanized or None
