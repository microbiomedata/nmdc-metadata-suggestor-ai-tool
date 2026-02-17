"""Utilities for DOI validation, normalization, and classification.

Provides functions to:
- Normalize DOI strings (strip URLs, prefixes, whitespace)
- Validate DOIs via the DOI Handle API
- Detect registration agency (Crossref vs DataCite) via doi.org/doiRA/
- Classify DOIs by resource type using agency-specific APIs
- Infer NMDC DoiCategoryEnum values from classification results

Prior art:
- CultureBotAI/MicroGrowAgents scripts/doi_validation/validate_failed_dois.py
  (Crossref/Semantic Scholar/Unpaywall waterfall; no Handle API or DataCite)
- cmungall/aurelian src/aurelian/utils/doi_fetcher.py
  (Crossref metadata + Unpaywall OA PDF discovery)
- contextualizer-ai/artl-mcp src/artl_mcp/utils/identifier_utils.py
  (DOI/PMID/PMCID normalization and type detection; no Handle API validation,
  no RA detection, no classification)

See also:
- DOI Handle API: https://doi.org/api/handles/
- Registration agency detection: https://doi.org/doiRA/
- Crossref types: https://api.crossref.org/types
- DataCite resourceTypeGeneral: https://datacite-metadata-schema.readthedocs.io/
- NMDC DoiCategoryEnum: https://microbiomedata.github.io/nmdc-schema/DoiCategoryEnum/
"""

import os
import re

import requests
from pydantic import BaseModel

# API endpoints
DOI_HANDLE_API = "https://doi.org/api/handles"
DOI_RA_API = "https://doi.org/doiRA"
CROSSREF_API = "https://api.crossref.org/works"
DATACITE_API = "https://api.datacite.org/dois"

DEFAULT_TIMEOUT = 15

# Contact email for API User-Agent headers (Crossref polite pool, OpenAlex).
# Read from environment; falls back to the NMDC default.
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "support@microbiomedata.org")
USER_AGENT = f"NMDCMetadataSuggestor/0.1 (mailto:{CONTACT_EMAIL})"

# DOI syntax: prefix (10.NNNN+) / suffix (any non-whitespace)
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")

# ---------------------------------------------------------------------------
# Mapping: external resource types -> NMDC DoiCategoryEnum
#
# Provenance of the three layers:
#
# 1. EXTERNAL CANONICAL — values returned by Crossref and DataCite APIs.
#    - Crossref "type" field: 30 values at https://api.crossref.org/types
#    - DataCite "resourceTypeGeneral": 33 values defined in DataCite Metadata
#      Schema 4.6 (Dec 2024).  Full list at
#      https://datacite-metadata-schema.readthedocs.io/
#    These are authoritative; we use them as-is and never invent new ones.
#
# 2. NMDC CANONICAL — the DoiCategoryEnum in nmdc-schema, defined in
#    src/schema/basic_slots.yaml with exactly 4 permissible values:
#      award_doi, dataset_doi, publication_doi, data_management_plan_doi
#    The schema cites the DataCite PDF (resourceTypeGeneral, pp 48-53) and
#    https://api.crossref.org/types as inspiration via see_also links.
#    Ref: https://microbiomedata.github.io/nmdc-schema/DoiCategoryEnum/
#
# 3. AD-HOC MAPPING (this file) — which external types map to which NMDC
#    category.  nmdc-schema does NOT define this mapping; it only links to
#    the external vocabularies.  Historically NMDC has assigned doi_category
#    manually per-DOI in schema migrators (e.g. migrator_from_8_1_to_9_0).
#    The sets below are our best-effort mapping.  They are conservative:
#    types not listed return None rather than guessing.
#
#    Unmapped Crossref types (return None):
#      component, standard, report-component, other, ...
#    Unmapped DataCite types (return None):
#      Software, Workflow, ComputationalNotebook, Instrument,
#      PhysicalObject, Collection, Image, ...
#    These could plausibly be publication_doi or dataset_doi but the right
#    NMDC category is ambiguous, so we leave them for human review.
# ---------------------------------------------------------------------------

# Crossref type -> NMDC DoiCategoryEnum
# Source values: https://api.crossref.org/types (external canonical)
CROSSREF_PUBLICATION_TYPES = {
    "journal-article",
    "book",
    "book-chapter",
    "book-section",
    "book-part",
    "proceedings-article",
    "posted-content",  # preprints (bioRxiv, medRxiv)
    "report",
    "dissertation",
    "monograph",
    "reference-entry",
    "peer-review",
}
CROSSREF_DATASET_TYPES = {"dataset"}
CROSSREF_AWARD_TYPES = {"grant"}

# DataCite resourceTypeGeneral -> NMDC DoiCategoryEnum
# Source values: DataCite Metadata Schema 4.6 (external canonical)
DATACITE_PUBLICATION_TYPES = {
    "JournalArticle",
    "Book",
    "BookChapter",
    "ConferencePaper",
    "Dissertation",
    "Preprint",
    "Report",
    "Text",
}
DATACITE_DATASET_TYPES = {"Dataset"}
DATACITE_AWARD_TYPES = {"Award"}  # added in DataCite Schema 4.6, Dec 2024
DATACITE_DMP_TYPES = {"OutputManagementPlan"}


class DoiValidation(BaseModel):
    """Result of DOI validation via the Handle API."""

    doi: str
    is_valid: bool
    handle_response_code: int | None = None
    error: str | None = None


class DoiClassification(BaseModel):
    """Classification of a DOI along multiple axes.

    Axes (from doi-categories-and-fetching-research-2026-02-17.md):
    1. Resource Type — what does the DOI point to?
    2. Registration Agency — Crossref or DataCite
    3. Publisher/Source — who publishes the content?
    Plus: inferred NMDC DoiCategoryEnum value
    """

    doi: str
    is_valid: bool
    registration_agency: str | None = None
    resource_type: str | None = None
    resource_type_general: str | None = None
    publisher: str | None = None
    prefix: str | None = None
    inferred_nmdc_category: str | None = None
    error: str | None = None


def normalize_doi(raw: str) -> str:
    """Normalize a DOI string to bare identifier form.

    Strips common URL wrappers, ``doi:`` prefix, whitespace, angle brackets,
    and trailing citation punctuation.

    Args:
        raw: Raw DOI string in any common format.

    Returns:
        Bare DOI string (e.g., ``10.1038/s41564-020-00861-0``).
    """
    doi = raw.strip()
    for url_prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
    ):
        if doi.lower().startswith(url_prefix):
            doi = doi[len(url_prefix) :]
            break
    if doi.lower().startswith("doi:"):
        doi = doi[4:]
    doi = doi.strip("<>")
    doi = doi.rstrip(".,")
    return doi


def validate_doi(doi: str) -> DoiValidation:
    """Validate a DOI via the DOI Handle API.

    Checks whether a DOI exists and resolves by querying
    ``https://doi.org/api/handles/{doi}``.

    Handle API response codes:
    - 1: SUCCESS (DOI exists and resolves)
    - 100: HANDLE_NOT_FOUND
    - 200: VALUES_NOT_FOUND
    - 2: ERROR

    Args:
        doi: Normalized DOI string.

    Returns:
        DoiValidation with ``is_valid=True`` if the DOI resolves.
    """
    if not DOI_PATTERN.match(doi):
        return DoiValidation(doi=doi, is_valid=False, error="Malformed DOI syntax")

    try:
        response = requests.get(
            f"{DOI_HANDLE_API}/{doi}",
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        data = response.json()
        code = data.get("responseCode")
        return DoiValidation(
            doi=doi,
            is_valid=code == 1,
            handle_response_code=code,
            error=None if code == 1 else f"Handle API responseCode={code}",
        )
    except requests.RequestException as e:
        return DoiValidation(doi=doi, is_valid=False, error=f"Request failed: {e}")


def detect_registration_agency(doi: str) -> str | None:
    """Detect the registration agency for a DOI.

    Queries ``https://doi.org/doiRA/{doi}`` to determine if a DOI is
    registered with Crossref, DataCite, or another agency.

    Args:
        doi: Normalized DOI string.

    Returns:
        Registration agency name (e.g., ``"Crossref"``, ``"DataCite"``)
        or ``None`` on error.
    """
    try:
        response = requests.get(
            f"{DOI_RA_API}/{doi}",
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and data:
            ra: str | None = data[0].get("RA")
            return ra
        return None
    except requests.RequestException:
        return None


def infer_nmdc_category(
    registration_agency: str | None,
    resource_type: str | None,
    resource_type_general: str | None,
) -> str | None:
    """Infer the NMDC DoiCategoryEnum value from API-provided type information.

    Maps Crossref work types and DataCite resourceTypeGeneral to one of:
    ``publication_doi``, ``dataset_doi``, ``award_doi``,
    ``data_management_plan_doi``, or ``None`` (unmappable).

    Args:
        registration_agency: ``"Crossref"`` or ``"DataCite"``.
        resource_type: Crossref ``type`` field or DataCite ``resourceType``.
        resource_type_general: DataCite ``resourceTypeGeneral`` (ignored for Crossref).

    Returns:
        NMDC DoiCategoryEnum string or ``None``.
    """
    if registration_agency == "Crossref" and resource_type:
        if resource_type in CROSSREF_PUBLICATION_TYPES:
            return "publication_doi"
        if resource_type in CROSSREF_DATASET_TYPES:
            return "dataset_doi"
        if resource_type in CROSSREF_AWARD_TYPES:
            return "award_doi"
    if registration_agency == "DataCite" and resource_type_general:
        if resource_type_general in DATACITE_PUBLICATION_TYPES:
            return "publication_doi"
        if resource_type_general in DATACITE_DATASET_TYPES:
            return "dataset_doi"
        if resource_type_general in DATACITE_AWARD_TYPES:
            return "award_doi"
        if resource_type_general in DATACITE_DMP_TYPES:
            return "data_management_plan_doi"
    return None


def classify_doi(doi: str) -> DoiClassification:
    """Classify a DOI by registration agency, resource type, and NMDC category.

    Combines RA detection with agency-specific metadata lookup. For DOIs outside
    the caller's scope (e.g., dataset DOIs when you need publications), the
    ``inferred_nmdc_category`` field tells you what it actually is.

    Args:
        doi: Normalized DOI string.

    Returns:
        DoiClassification with all available axes populated.
    """
    prefix_match = re.match(r"^(10\.\d{4,9})/", doi)
    prefix = prefix_match.group(1) if prefix_match else None

    if not DOI_PATTERN.match(doi):
        return DoiClassification(
            doi=doi, is_valid=False, prefix=prefix, error="Malformed DOI syntax"
        )

    ra = detect_registration_agency(doi)
    if ra is None:
        return DoiClassification(
            doi=doi,
            is_valid=False,
            prefix=prefix,
            error="Could not detect registration agency (DOI may not exist)",
        )

    if ra == "Crossref":
        return _classify_crossref(doi, prefix, ra)
    if ra == "DataCite":
        return _classify_datacite(doi, prefix, ra)

    # Other RA (mEDRA, ISTIC, etc.) — valid but we can't classify further
    return DoiClassification(doi=doi, is_valid=True, prefix=prefix, registration_agency=ra)


def _classify_crossref(doi: str, prefix: str | None, ra: str) -> DoiClassification:
    """Classify a Crossref DOI by querying the Crossref API."""
    try:
        response = requests.get(
            f"{CROSSREF_API}/{doi}",
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        msg = response.json().get("message", {})
        resource_type = msg.get("type")
        return DoiClassification(
            doi=doi,
            is_valid=True,
            registration_agency=ra,
            resource_type=resource_type,
            publisher=msg.get("publisher"),
            prefix=prefix,
            inferred_nmdc_category=infer_nmdc_category(ra, resource_type, None),
        )
    except requests.RequestException as e:
        return DoiClassification(
            doi=doi,
            is_valid=True,
            prefix=prefix,
            registration_agency=ra,
            error=f"Crossref API error: {e}",
        )


def _classify_datacite(doi: str, prefix: str | None, ra: str) -> DoiClassification:
    """Classify a DataCite DOI by querying the DataCite API."""
    try:
        response = requests.get(
            f"{DATACITE_API}/{doi}",
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        attrs = response.json().get("data", {}).get("attributes", {})
        types = attrs.get("types", {})
        resource_type = types.get("resourceType")
        resource_type_general = types.get("resourceTypeGeneral")
        return DoiClassification(
            doi=doi,
            is_valid=True,
            registration_agency=ra,
            resource_type=resource_type,
            resource_type_general=resource_type_general,
            publisher=attrs.get("publisher"),
            prefix=prefix,
            inferred_nmdc_category=infer_nmdc_category(ra, resource_type, resource_type_general),
        )
    except requests.RequestException as e:
        return DoiClassification(
            doi=doi,
            is_valid=True,
            prefix=prefix,
            registration_agency=ra,
            error=f"DataCite API error: {e}",
        )
