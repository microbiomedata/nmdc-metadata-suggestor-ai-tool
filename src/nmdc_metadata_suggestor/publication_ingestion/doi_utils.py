"""Utilities for DOI validation, normalization, and classification.

See ``docs/doi-classification-design.md`` for design rationale, value
provenance, and the full list of unmapped types.
"""

import os
import re
import time
from email.utils import parsedate_to_datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from nmdc_metadata_suggestor.models.doi import (
    DoiCategory,
    DoiClassification,
    DoiValidation,
)

# API endpoints
DOI_HANDLE_API = "https://doi.org/api/handles"
DOI_RA_API = "https://doi.org/doiRA"
CROSSREF_API = "https://api.crossref.org/works"
DATACITE_API = "https://api.datacite.org/dois"

DEFAULT_TIMEOUT = 15
DEFAULT_RETRY_ATTEMPTS = int(os.environ.get("NMDC_HTTP_RETRY_ATTEMPTS", "3"))
# Default backoff is 0 to avoid introducing real sleep delays by default (e.g., in tests).
DEFAULT_RETRY_BACKOFF_SECONDS = float(os.environ.get("NMDC_HTTP_RETRY_BACKOFF_SECONDS", "0"))
RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
SESSION_POOL_CONNECTIONS = int(os.environ.get("NMDC_HTTP_POOL_CONNECTIONS", "20"))
SESSION_POOL_MAXSIZE = int(os.environ.get("NMDC_HTTP_POOL_MAXSIZE", "100"))

# Contact email for API User-Agent headers (Crossref polite pool, OpenAlex).
# Read from environment; falls back to the NMDC default.
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "support@microbiomedata.org")
USER_AGENT = f"NMDCMetadataSuggestor/0.1 (mailto:{CONTACT_EMAIL})"

# DOI syntax: prefix (10.NNNN+) / suffix (any non-whitespace)
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")

_HTTP_SESSION = requests.Session()
_HTTP_ADAPTER = HTTPAdapter(
    pool_connections=SESSION_POOL_CONNECTIONS,
    pool_maxsize=SESSION_POOL_MAXSIZE,
    max_retries=0,
)
_HTTP_SESSION.mount("http://", _HTTP_ADAPTER)
_HTTP_SESSION.mount("https://", _HTTP_ADAPTER)


def request_with_retry(
    method: str,
    url: str,
    *,
    max_attempts: int = DEFAULT_RETRY_ATTEMPTS,
    retry_status_codes: set[int] | frozenset[int] = RETRY_STATUS_CODES,
    backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    timeout: int = DEFAULT_TIMEOUT,
    **request_kwargs: Any,
) -> requests.Response:
    """Perform an HTTP request with retry/backoff for transient failures.

    Retries are applied for:
    - Network/request exceptions (e.g. connection resets, timeouts)
    - HTTP response status codes in ``retry_status_codes`` (default: 429/5xx)

    ``Retry-After`` is honored when present; otherwise exponential backoff is used.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    last_exception: requests.RequestException | None = None
    response: requests.Response | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = _HTTP_SESSION.request(method, url, timeout=timeout, **request_kwargs)
            if response.status_code not in retry_status_codes:
                return response

            if attempt == max_attempts:
                return response

            delay = _retry_delay_seconds(
                response.headers.get("Retry-After"), attempt, backoff_seconds
            )
            _close_response_quietly(response)
            response = None
            if delay > 0:
                time.sleep(delay)
        except requests.RequestException as exc:
            last_exception = exc
            if attempt == max_attempts:
                raise
            _close_response_quietly(getattr(exc, "response", None))
            delay = backoff_seconds * (2 ** (attempt - 1))
            if delay > 0:
                time.sleep(delay)

    if response is not None:
        return response
    if last_exception is not None:
        raise last_exception
    raise RuntimeError("request_with_retry exited without response or exception")


def _retry_delay_seconds(retry_after: str | None, attempt: int, backoff_seconds: float) -> float:
    """Return retry delay from Retry-After header or exponential fallback."""
    if isinstance(retry_after, str) and retry_after.strip():
        retry_after_value = retry_after.strip()
        if retry_after_value.isdigit():
            return max(0.0, float(retry_after_value))
        try:
            retry_dt = parsedate_to_datetime(retry_after_value)
            return max(0.0, retry_dt.timestamp() - time.time())
        except (TypeError, ValueError):
            pass

    return backoff_seconds * (2 ** (attempt - 1))


def _close_response_quietly(response: requests.Response | None) -> None:
    """Close an HTTP response while suppressing close-time errors."""
    if response is None:
        return
    try:
        response.close()
    except requests.RequestException:
        return


# ---------------------------------------------------------------------------
# Mapping: external resource types -> NMDC DoiCategoryEnum
#
# Keys are canonical values from Crossref/DataCite APIs.
# Values are DoiCategory enum members (mirrors nmdc-schema DoiCategoryEnum).
# Types not listed here return None (ambiguous, requires human review).
# See docs/doi-classification-design.md §2 for provenance and unmapped types.
# ---------------------------------------------------------------------------

CROSSREF_TYPE_TO_NMDC: dict[str, DoiCategory] = {
    # Publications
    "journal-article": DoiCategory.PUBLICATION,
    "book": DoiCategory.PUBLICATION,
    "book-chapter": DoiCategory.PUBLICATION,
    "book-section": DoiCategory.PUBLICATION,
    "book-part": DoiCategory.PUBLICATION,
    "proceedings-article": DoiCategory.PUBLICATION,
    "posted-content": DoiCategory.PUBLICATION,  # preprints (bioRxiv, medRxiv)
    "report": DoiCategory.PUBLICATION,
    "dissertation": DoiCategory.PUBLICATION,
    "monograph": DoiCategory.PUBLICATION,
    "reference-entry": DoiCategory.PUBLICATION,
    "peer-review": DoiCategory.PUBLICATION,
    # Datasets
    "dataset": DoiCategory.DATASET,
    # Awards
    "grant": DoiCategory.AWARD,
}

DATACITE_TYPE_TO_NMDC: dict[str, DoiCategory] = {
    # Publications (keyed on resourceTypeGeneral)
    "JournalArticle": DoiCategory.PUBLICATION,
    "Book": DoiCategory.PUBLICATION,
    "BookChapter": DoiCategory.PUBLICATION,
    "ConferencePaper": DoiCategory.PUBLICATION,
    "Dissertation": DoiCategory.PUBLICATION,
    "Preprint": DoiCategory.PUBLICATION,
    "Report": DoiCategory.PUBLICATION,
    "Text": DoiCategory.PUBLICATION,
    # Datasets
    "Dataset": DoiCategory.DATASET,
    # Awards
    "Award": DoiCategory.AWARD,
    # Data Management Plans
    "OutputManagementPlan": DoiCategory.DATA_MANAGEMENT_PLAN,
}


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
    except (requests.RequestException, ValueError) as e:
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
    except (requests.RequestException, ValueError):
        return None


def infer_nmdc_category(
    registration_agency: str | None,
    resource_type: str | None,
    resource_type_general: str | None,
) -> str | None:
    """Infer the NMDC DoiCategoryEnum value from API-provided type information.

    Maps Crossref work types and DataCite resourceTypeGeneral values to
    :class:`~nmdc_metadata_suggestor.models.doi.DoiCategory` members.
    Returns ``None`` for unmapped types (see design doc §2 for the full list).

    Args:
        registration_agency: ``"Crossref"`` or ``"DataCite"``.
        resource_type: Crossref ``type`` field or DataCite ``resourceType``.
        resource_type_general: DataCite ``resourceTypeGeneral`` (ignored for Crossref).

    Returns:
        NMDC DoiCategoryEnum string or ``None``.
    """
    if registration_agency == "Crossref" and resource_type:
        cat = CROSSREF_TYPE_TO_NMDC.get(resource_type)
        if cat is not None:
            return cat.value
    if registration_agency == "DataCite" and resource_type_general:
        cat = DATACITE_TYPE_TO_NMDC.get(resource_type_general)
        if cat is not None:
            return cat.value
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
        # RA detection can fail due to transient network errors, not just
        # nonexistent DOIs. Return is_valid=True so the abstract waterfall
        # can still proceed; callers can check the error field if needed.
        return DoiClassification(
            doi=doi,
            is_valid=True,
            prefix=prefix,
            error="Could not detect registration agency (network error or DOI may not exist)",
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
    except (requests.RequestException, ValueError) as e:
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
    except (requests.RequestException, ValueError) as e:
        return DoiClassification(
            doi=doi,
            is_valid=True,
            prefix=prefix,
            registration_agency=ra,
            error=f"DataCite API error: {e}",
        )
