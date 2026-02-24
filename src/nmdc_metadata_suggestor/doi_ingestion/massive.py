"""MassIVE DOI resolver via ProteomeCentral PROXI."""

import re

import requests

from nmdc_metadata_suggestor.constants import (
    DATACITE_API_URL,
    DEFAULT_TIMEOUT,
    DOI_CONTENT_NEGOTIATION_API,
    PROXI_DATASETS_API,
    USER_AGENT,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    request_with_retry,
    text_mentions_doi,
)
from nmdc_metadata_suggestor.doi_ingestion.resolver_context import ResolverContext


def try_massive(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return cleaned/raw context from MassIVE via ProteomeCentral lookups."""
    for accession in _collect_massive_accession_candidates(doi, errors=errors):
        payload = _fetch_proxi_dataset(accession, errors=errors)
        if payload is None:
            continue
        context = _extract_massive_context(payload)
        if context is not None:
            return context

    for accession in _search_proxi_accessions_by_doi(doi, errors=errors):
        payload = _fetch_proxi_dataset(accession, errors=errors)
        if payload is None:
            continue
        context = _extract_massive_context(payload)
        if context is not None:
            return context

    context = _extract_massive_context_from_datacite_titles(doi, errors=errors)
    if context is not None:
        return context
    append_error(errors, "MassIVE/PROXI lookup returned no usable context")
    return None


def _collect_massive_accession_candidates(doi: str, errors: list[str] | None = None) -> list[str]:
    """Collect likely ProteomeXchange/MassIVE accessions for a DOI."""
    candidates: list[str] = []
    seen: set[str] = set()

    for accession in _extract_massive_accessions(doi):
        if accession not in seen:
            seen.add(accession)
            candidates.append(accession)

    location = _resolve_doi_redirect_location(doi, errors=errors)
    if location is not None:
        for accession in _extract_massive_accessions(location):
            if accession not in seen:
                seen.add(accession)
                candidates.append(accession)

    return candidates


def _resolve_doi_redirect_location(doi: str, errors: list[str] | None = None) -> str | None:
    """Resolve DOI redirect location without following downstream redirects."""
    try:
        response = request_with_retry(
            "GET",
            f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        append_error(errors, f"DOI redirect lookup failed: {exc.__class__.__name__}")
        return None

    if response.status_code not in {301, 302, 303, 307, 308}:
        append_error(errors, f"DOI redirect lookup returned HTTP {response.status_code}")
        return None

    location = response.headers.get("Location")
    if not isinstance(location, str) or not location.strip():
        return None
    return location


def _extract_massive_accessions(text: str) -> list[str]:
    """Extract unique ProteomeXchange/MassIVE accession tokens from text."""
    matches = re.findall(r"(PXD\d{6,}|MSV\d{6,})", text.upper())
    seen: set[str] = set()
    accessions: list[str] = []
    for match in matches:
        if match in seen:
            continue
        seen.add(match)
        accessions.append(match)
    return accessions


def _search_proxi_accessions_by_doi(doi: str, errors: list[str] | None = None) -> list[str]:
    """Search PROXI dataset rows for publication cells mentioning the DOI."""
    query_values = [doi, f"https://doi.org/{doi}", f"doi:{doi}"]
    seen: set[str] = set()
    accessions: list[str] = []

    for publication_query in query_values:
        rows = _fetch_proxi_dataset_rows(publication_query, errors=errors)
        for row in rows:
            accession = _extract_proxi_row_accession_for_doi(row, doi)
            if accession is None or accession in seen:
                continue
            seen.add(accession)
            accessions.append(accession)

    return accessions


def _fetch_proxi_dataset_rows(
    publication_query: str, errors: list[str] | None = None
) -> list[list[object]]:
    """Return PROXI dataset table rows for a publication-filtered query."""
    try:
        response = request_with_retry(
            "GET",
            PROXI_DATASETS_API,
            params={
                "publication": publication_query,
                "repository": "MassIVE",
                "resultType": "full",
                "pageSize": 100,
                "pageNumber": 1,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"PROXI dataset rows request returned HTTP {response.status_code}")
            return []
        payload = response.json()
    except requests.RequestException as exc:
        append_error(errors, f"PROXI dataset rows request failed: {exc.__class__.__name__}")
        return []
    except ValueError:
        append_error(errors, "PROXI dataset rows request returned invalid JSON")
        return []

    datasets = payload.get("datasets")
    if not isinstance(datasets, list):
        return []

    rows: list[list[object]] = []
    for row in datasets:
        if isinstance(row, list):
            rows.append(row)
    return rows


def _extract_proxi_row_accession_for_doi(row: list[object], doi: str) -> str | None:
    """Return dataset accession if a PROXI row references the DOI."""
    if not row:
        return None

    accession = row[0] if len(row) > 0 else None
    publication_cell = row[7] if len(row) > 7 else None
    if not isinstance(accession, str):
        return None
    if not isinstance(publication_cell, str):
        return None
    if not text_mentions_doi(publication_cell, doi):
        return None

    cleaned_accession = accession.strip().upper()
    if not cleaned_accession:
        return None
    return cleaned_accession


def _fetch_proxi_dataset(
    accession: str, errors: list[str] | None = None
) -> dict[str, object] | None:
    """Fetch detailed dataset metadata for a ProteomeXchange accession."""
    try:
        response = request_with_retry(
            "GET",
            f"{PROXI_DATASETS_API}/{accession}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"PROXI dataset request returned HTTP {response.status_code}")
            return None
        payload = response.json()
    except requests.RequestException as exc:
        append_error(errors, f"PROXI dataset request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(errors, "PROXI dataset request returned invalid JSON")
        return None

    if isinstance(payload, dict):
        return payload
    return None


def _extract_massive_context(payload: dict[str, object]) -> ResolverContext | None:
    """Extract best available context from a PROXI dataset payload."""
    status = payload.get("status")
    if isinstance(status, str) and status.strip().lower() == "error":
        return None
    if isinstance(status, dict):
        status_value = status.get("status")
        if isinstance(status_value, str) and status_value.strip().lower() == "error":
            return None

    for key in ("description", "dataset_description", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = clean_text(value)
            if cleaned:
                return ResolverContext(text=cleaned, raw_text=value, kind="description")

    for key in ("title", "dataset_title"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = clean_text(value)
            if cleaned:
                return ResolverContext(text=cleaned, raw_text=value, kind="description")
    return None


def _extract_massive_context_from_datacite_titles(
    doi: str, errors: list[str] | None = None
) -> ResolverContext | None:
    """Fallback: derive MassIVE context from DataCite subtitle/title metadata."""
    try:
        response = request_with_retry(
            "GET",
            f"{DATACITE_API_URL}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"DataCite API returned HTTP {response.status_code}")
            return None
        attrs = response.json().get("data", {}).get("attributes", {})
    except requests.RequestException as exc:
        append_error(errors, f"DataCite API request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(errors, "DataCite API returned invalid JSON")
        return None

    titles = attrs.get("titles")
    if not isinstance(titles, list):
        return None

    preferred = _pick_massive_datacite_title(titles)
    if preferred is None:
        return None

    cleaned = clean_text(preferred)
    if not cleaned:
        return None
    return ResolverContext(text=cleaned, raw_text=preferred, kind="description")


def _pick_massive_datacite_title(titles: list[object]) -> str | None:
    """Pick the most informative DataCite title entry for MassIVE context."""
    subtitle_candidate: str | None = None
    title_candidate: str | None = None

    for item in titles:
        if not isinstance(item, dict):
            continue
        text = item.get("title")
        if not isinstance(text, str) or not text.strip():
            continue

        title_type = item.get("titleType")
        if isinstance(title_type, str) and title_type.lower() == "subtitle":
            if subtitle_candidate is None:
                subtitle_candidate = text
        elif title_candidate is None:
            title_candidate = text

    if subtitle_candidate is not None:
        return subtitle_candidate
    if title_candidate is not None:
        return title_candidate
    return None
