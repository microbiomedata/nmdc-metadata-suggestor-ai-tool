"""Finding the data deposits that belong to a publication.

Two routes, in decreasing order of reliability: the article's own relation
metadata (Crossref ``relation`` / DataCite ``relatedIdentifiers``), and DOIs
mined from prose such as a "Data Availability" statement. Sequence/proteomics
accessions are recognized too, but only to surface them -- they point at raw
omics data, which is out of scope for metadata suggestion.
"""

import re

from nmdc_metadata_suggestor_ai_tool.constants import (
    CROSSREF_API_URL,
    DATACITE_API_URL,
    DEFAULT_TIMEOUT,
    DRYAD_DOI_PREFIX,
    USER_AGENT,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    normalize_doi,
    request_with_retry,
)

# DOI prefix -> the supplement source that can fetch that repository's files.
REPO_BY_PREFIX: dict[str, str] = {
    DRYAD_DOI_PREFIX: "dryad",
    "10.5281": "zenodo",
    "10.6084": "figshare",
}

# Crossref relation types / DataCite relationTypes that point at *data* related
# to the article (as opposed to citations). Compared after lowercasing and
# stripping non-letters, so "IsSupplementedBy" and "is-supplemented-by" match.
DATA_RELATION_TYPES: frozenset[str] = frozenset(
    {
        "issupplementedby",
        "haspart",
        "issourceof",
        "isderivedfrom",
        "isdocumentedby",
    }
)


def normalize_relation(relation_type: str) -> str:
    return re.sub(r"[^a-z]", "", relation_type.lower())


def repo_for_doi(doi: str) -> str | None:
    """Return the supplement source able to fetch *doi*, or None."""
    prefix = normalize_doi(doi).split("/", 1)[0]
    return REPO_BY_PREFIX.get(prefix)


def find_related_data_dois(doi: str) -> list[str]:
    """Find data-repository DOIs linked from a publication's relation metadata.

    Reads Crossref ``relation`` and DataCite ``relatedIdentifiers``, keeps entries
    whose relation indicates associated data (see ``DATA_RELATION_TYPES``) and
    whose target is a DOI we can fetch (Dryad/Zenodo/Figshare).

    Returns:
        Deduplicated, normalized data-repository DOIs (empty on any failure).
    """
    doi = normalize_doi(doi)
    found: list[str] = []
    found.extend(crossref_related_dois(doi))
    found.extend(datacite_related_dois(doi))

    unique: list[str] = []
    for candidate in found:
        normalized = normalize_doi(candidate)
        if repo_for_doi(normalized) and normalized not in unique and normalized != doi:
            unique.append(normalized)
    return unique


def crossref_related_dois(doi: str) -> list[str]:
    """Extract related DOIs from a Crossref work's ``relation`` block."""
    try:
        response = request_with_retry(
            "GET",
            f"{CROSSREF_API_URL}/{doi}",
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        relation = response.json().get("message", {}).get("relation", {})
    except Exception:
        return []

    dois: list[str] = []
    if isinstance(relation, dict):
        for relation_type, entries in relation.items():
            if normalize_relation(str(relation_type)) not in DATA_RELATION_TYPES:
                continue
            for entry in entries if isinstance(entries, list) else []:
                if isinstance(entry, dict) and entry.get("id-type") == "doi":
                    identifier = entry.get("id")
                    if isinstance(identifier, str):
                        dois.append(identifier)
    return dois


def datacite_related_dois(doi: str) -> list[str]:
    """Extract related DOIs from a DataCite record's ``relatedIdentifiers``."""
    try:
        response = request_with_retry(
            "GET",
            f"{DATACITE_API_URL}/{doi}",
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        attributes = response.json().get("data", {}).get("attributes", {})
        related = attributes.get("relatedIdentifiers", [])
    except Exception:
        return []

    dois: list[str] = []
    for entry in related if isinstance(related, list) else []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("relatedIdentifierType", "")).lower() != "doi":
            continue
        if normalize_relation(str(entry.get("relationType", ""))) not in DATA_RELATION_TYPES:
            continue
        identifier = entry.get("relatedIdentifier")
        if isinstance(identifier, str):
            dois.append(identifier)
    return dois


# ---------------------------------------------------------------------------
# Text / accession mining (fallback when relation metadata is absent)
# ---------------------------------------------------------------------------

# Repository-specific DOI shapes. Matching the exact form (rather than a generic
# DOI regex + prefix filter) stops trailing prose from riding along on a mined DOI
# -- e.g. "10.5281/zenodo.123and" yields "10.5281/zenodo.123", not the whole run.
REPO_DOI_PATTERN = re.compile(
    r"10\.5281/zenodo\.\d+"  # Zenodo
    r"|10\.6084/m9\.figshare\.\d+(?:\.v\d+)?"  # Figshare (optional version)
    r"|10\.5061/dryad\.[a-z0-9]+(?:\.\d+)?",  # Dryad (optional version)
    re.IGNORECASE,
)

# Common sequence/proteomics repository accession patterns. Detected and surfaced
# for awareness, but NOT retrieved (they point at raw omics data, out of scope).
ACCESSION_PATTERN = re.compile(
    r"\b("
    r"PRJ[EDN][A-Z]\d+"  # BioProject
    r"|SAM[EDN][A-Z]?\d+"  # BioSample
    r"|[SED]R[RXPS]\d+"  # SRA/ENA runs, experiments, projects, samples
    r"|GSE\d+|GSM\d+"  # GEO
    r"|PXD\d+|MSV\d{9}"  # ProteomeXchange / MassIVE
    r")\b"
)


def extract_dataset_dois_from_text(text: str) -> list[str]:
    """Return data-repository DOIs (Dryad/Zenodo/Figshare) mentioned in *text*.

    Uses repository-specific DOI shapes so trailing text can't be captured as
    part of a matched DOI. DOIs are lowercased (canonical for these repositories).
    """
    found: list[str] = []
    for match in REPO_DOI_PATTERN.finditer(text):
        candidate = normalize_doi(match.group(0)).lower()
        if candidate not in found:
            found.append(candidate)
    return found


def extract_accessions_from_text(text: str) -> list[str]:
    """Return sequence/proteomics accessions mentioned in *text* (deduplicated)."""
    seen: list[str] = []
    for match in ACCESSION_PATTERN.finditer(text):
        accession = match.group(1)
        if accession not in seen:
            seen.append(accession)
    return seen
