"""EMSL DOI resolver."""

import re

import requests

from nmdc_metadata_suggestor.doi_ingestion.common import clean_text
from nmdc_metadata_suggestor.publication_ingestion.doi_utils import (
    DEFAULT_TIMEOUT,
    USER_AGENT,
    normalize_doi,
)

EMSL_PROJECTS_API = "https://api.emsl.pnnl.gov/external/projects"


def try_emsl(doi: str) -> tuple[str, str, str] | None:
    """Return cleaned/raw context from EMSL project details resolved by DOI."""
    project_id = _extract_emsl_project_id(doi)
    if project_id is None:
        return None

    try:
        response = requests.get(
            f"{EMSL_PROJECTS_API}/{project_id}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    award_doi = payload.get("award_doi")
    if isinstance(award_doi, str) and award_doi:
        if normalize_doi(award_doi) != doi:
            return None

    abstract = payload.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        cleaned = clean_text(abstract)
        if cleaned:
            return cleaned, abstract, "abstract"

    for key in ("title", "project_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = clean_text(value)
            if cleaned:
                return cleaned, value, "description"
    return None


def _extract_emsl_project_id(doi: str) -> str | None:
    """Extract EMSL project ID embedded in award DOI suffix."""
    # Example: 10.46936/jejc.proj.2014.48483/60005501 -> 48483
    match = re.search(r"\.proj\.\d{4}\.(\d+)(?:/|$)", doi)
    if match is None:
        return None
    return match.group(1)
