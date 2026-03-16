"""EMSL DOI resolver."""

import re

import requests

from nmdc_metadata_suggestor.constants import (
    DEFAULT_TIMEOUT,
    EMSL_PROJECTS_API,
    EMSL_SCIENCECENTRAL_STUDY_LIGHT_API,
    USER_AGENT,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    normalize_doi,
    request_with_retry,
)
from nmdc_metadata_suggestor.models.resolver_context import ResolverContext


def try_emsl(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return cleaned/raw context from EMSL ScienceCentral or external project APIs."""
    project_id = _extract_emsl_project_id(doi)
    if project_id is None:
        append_error(errors, "Could not extract EMSL project id from DOI")
        return None

    sciencecentral_context = _fetch_emsl_sciencecentral_context(
        project_id, errors=errors)
    if sciencecentral_context is not None:
        return sciencecentral_context

    external_context = _fetch_emsl_external_context(
        project_id, requested_doi=doi, errors=errors)
    if external_context is not None:
        return external_context

    append_error(errors, "EMSL APIs contained no abstract/description fields")
    return None


def _extract_emsl_project_id(doi: str) -> str | None:
    """Extract EMSL project ID embedded in award DOI suffix."""
    # Example: 10.46936/jejc.proj.2014.48483/60005501 -> 48483
    match = re.search(r"\.proj\.\d{4}\.(\d+)(?:/|$)", doi)
    if match is None:
        return None
    return match.group(1)


def _fetch_emsl_sciencecentral_context(
    project_id: str,
    errors: list[str] | None = None,
) -> ResolverContext | None:
    """Return EMSL context from the ScienceCentral study search API."""
    try:
        response = request_with_retry(
            "POST",
            EMSL_SCIENCECENTRAL_STUDY_LIGHT_API,
            headers={"User-Agent": USER_AGENT},
            json={
                "filters": [
                    {
                        "operation": "in_list",
                        "value": [project_id],
                        "property": "project_id",
                    }
                ]
            },
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(
                errors,
                f"EMSL ScienceCentral API returned HTTP {response.status_code}",
            )
            return None
        payload = response.json()
    except requests.RequestException as exc:
        append_error(
            errors,
            f"EMSL ScienceCentral API request failed: {exc.__class__.__name__}",
        )
        return None
    except ValueError:
        append_error(errors, "EMSL ScienceCentral API returned invalid JSON")
        return None

    project = _extract_emsl_sciencecentral_project(payload, project_id)
    if project is None:
        append_error(
            errors, "EMSL ScienceCentral API returned no matching study")
        return None
    return _extract_emsl_context(project)


def _extract_emsl_sciencecentral_project(
    payload: object, project_id: str
) -> dict[str, object] | None:
    """Return the matching ScienceCentral study document source for a project id."""
    if not isinstance(payload, dict):
        return None
    hits = payload.get("hits")
    if not isinstance(hits, dict):
        return None
    hit_entries = hits.get("hits")
    if not isinstance(hit_entries, list):
        return None

    fallback: dict[str, object] | None = None
    for entry in hit_entries:
        if not isinstance(entry, dict):
            continue
        source = entry.get("_source")
        if not isinstance(source, dict):
            continue
        if fallback is None:
            fallback = source
        source_id = source.get("id")
        if isinstance(source_id, str) and source_id.strip() == project_id:
            return source
    return fallback


def _fetch_emsl_external_context(
    project_id: str,
    requested_doi: str,
    errors: list[str] | None = None,
) -> ResolverContext | None:
    """Return EMSL context from the external projects API."""
    try:
        response = request_with_retry(
            "GET",
            f"{EMSL_PROJECTS_API}/{project_id}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(
                errors, f"EMSL API returned HTTP {response.status_code}")
            return None
        payload = response.json()
    except requests.RequestException as exc:
        append_error(
            errors, f"EMSL API request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(errors, "EMSL API returned invalid JSON")
        return None

    award_doi = payload.get("award_doi")
    if isinstance(award_doi, str) and award_doi:
        if normalize_doi(award_doi) != requested_doi:
            append_error(errors, f"EMSL award_doi mismatch: {award_doi}")
            return None

    return _extract_emsl_context(payload)


def _extract_emsl_context(payload: dict[str, object]) -> ResolverContext | None:
    """Extract context text from an EMSL payload."""
    abstract = payload.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        cleaned = clean_text(abstract)
        if cleaned:
            return ResolverContext(
                text=cleaned,
                raw_text=abstract,
                kind="abstract",
            )

    for key in ("title", "project_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = clean_text(value)
            if cleaned:
                return ResolverContext(
                    text=cleaned,
                    raw_text=value,
                    kind="description",
                )
    return None
