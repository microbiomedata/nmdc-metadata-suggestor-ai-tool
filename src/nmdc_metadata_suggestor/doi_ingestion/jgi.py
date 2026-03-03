"""JGI DOI resolver."""

import re

import requests

from nmdc_metadata_suggestor.constants import DEFAULT_TIMEOUT, JGI_SEARCH_API, USER_AGENT
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    normalize_doi,
    request_with_retry,
)
from nmdc_metadata_suggestor.models.resolver_context import ResolverContext


def try_jgi(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return cleaned/raw context from JGI search response."""
    query_candidates: list[tuple[str, bool]] = []

    project_query = _extract_jgi_project_query(doi)
    if project_query:
        query_candidates.append((project_query, True))
    query_candidates.append((doi, False))

    seen_queries: set[str] = set()
    for query_value, use_project_field in query_candidates:
        if query_value in seen_queries:
            continue
        seen_queries.add(query_value)

        params: dict[str, str] = {"q": query_value, "api_version": "2", "x": "10", "p": "1"}
        if use_project_field:
            params["f"] = "project_id"

        try:
            response = request_with_retry(
                "GET",
                JGI_SEARCH_API,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=DEFAULT_TIMEOUT,
            )
            if response.status_code != 200:
                append_error(errors, f"JGI API returned HTTP {response.status_code}")
                continue
            payload = response.json()
        except requests.RequestException as exc:
            append_error(errors, f"JGI API request failed: {exc.__class__.__name__}")
            continue
        except ValueError:
            append_error(errors, "JGI API returned invalid JSON")
            continue

        context = _extract_jgi_context(payload, doi)
        if context is not None:
            return context

    append_error(errors, "JGI API returned no matching abstract/description")
    return None


def _extract_jgi_project_query(doi: str) -> str | None:
    """Extract likely JGI project identifier from DOI suffix."""
    match = re.match(r"^10\.25585/([^/\s]+)", doi)
    if match is None:
        return None
    token = match.group(1).strip()
    if not token:
        return None
    numeric = re.search(r"\d{4,}", token)
    if numeric is not None:
        return numeric.group(0)
    return token


def _extract_jgi_context(payload: object, requested_doi: str) -> ResolverContext | None:
    """Extract preferred context text from JGI search payload."""
    if not isinstance(payload, dict):
        return None

    proposals = payload.get("proposals")
    if isinstance(proposals, list):
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            if not _jgi_entry_matches_doi(proposal, requested_doi):
                continue

            for key in ("abstract", "lay_description", "description"):
                value = proposal.get(key)
                if isinstance(value, str) and value.strip():
                    cleaned = clean_text(value)
                    if cleaned:
                        kind = "abstract" if key == "abstract" else "description"
                        return ResolverContext(text=cleaned, raw_text=value, kind=kind)

            for key in ("title", "project_name", "name"):
                value = proposal.get(key)
                if isinstance(value, str) and value.strip():
                    cleaned = clean_text(value)
                    if cleaned:
                        return ResolverContext(text=cleaned, raw_text=value, kind="description")

    organisms = payload.get("organisms")
    if isinstance(organisms, list):
        for organism in organisms:
            if not isinstance(organism, dict):
                continue
            for key in ("title", "name"):
                value = organism.get(key)
                if isinstance(value, str) and value.strip():
                    cleaned = clean_text(value)
                    if cleaned:
                        return ResolverContext(text=cleaned, raw_text=value, kind="description")
    return None


def _jgi_entry_matches_doi(entry: dict[str, object], requested_doi: str) -> bool:
    """Return True when DOI-bearing JGI entry matches the requested DOI."""
    doi_keys = ("doi", "award_doi", "proposal_doi")
    for key in doi_keys:
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        if normalize_doi(value) == requested_doi:
            return True
    # Only treat entries with an explicitly matching DOI as matches.
    # If no DOI fields are present, this entry should not match the requested DOI.
    return False
