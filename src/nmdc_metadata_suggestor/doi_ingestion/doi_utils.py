"""Utilities for DOI validation, normalization, and classification.

See ``docs/doi-classification-design.md`` for design rationale, value
provenance, and the full list of unmapped types.
"""

import html
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from nmdc_metadata_suggestor.constants import (
    CROSSREF_API_URL,
    DATACITE_API_URL,
    DEFAULT_TIMEOUT,
    DOI_HANDLE_API,
    DOI_PATTERN,
    DOI_RA_API,
    HANDLE_RESPONSE_SUCCESS,
    USER_AGENT,
)
from nmdc_metadata_suggestor.models.doi import (
    DoiCategory,
    DoiClassification,
    DoiValidation,
)

DEFAULT_RETRY_ATTEMPTS = int(os.environ.get("NMDC_HTTP_RETRY_ATTEMPTS", "3"))
# Default backoff is 0 to avoid introducing real sleep delays by default (e.g., in tests).
DEFAULT_RETRY_BACKOFF_SECONDS = float(os.environ.get("NMDC_HTTP_RETRY_BACKOFF_SECONDS", "0"))
MAX_RETRY_DELAY_SECONDS = float(os.environ.get("NMDC_HTTP_MAX_RETRY_DELAY_SECONDS", "30"))
RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
SESSION_POOL_CONNECTIONS = int(os.environ.get("NMDC_HTTP_POOL_CONNECTIONS", "20"))
SESSION_POOL_MAXSIZE = int(os.environ.get("NMDC_HTTP_POOL_MAXSIZE", "100"))

LOGGER = logging.getLogger(__name__)
DOI_REFERENCE_PATTERN = re.compile(
    r"(?:https?://doi\.org/|doi:)?10\.\d{4,9}/\S+",
    re.IGNORECASE,
)
TRAILING_DOI_DELIMITERS = ".,;:]}>\"'"

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
    max_retry_delay_seconds: float = MAX_RETRY_DELAY_SECONDS,
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
                response.headers.get("Retry-After"),
                attempt,
                backoff_seconds,
                max_retry_delay_seconds,
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
            delay = _cap_retry_delay_seconds(
                backoff_seconds * (2 ** (attempt - 1)), max_retry_delay_seconds
            )
            if delay > 0:
                time.sleep(delay)

    if response is not None:
        return response
    if last_exception is not None:
        raise last_exception
    raise RuntimeError("request_with_retry exited without response or exception")


def _retry_delay_seconds(
    retry_after: str | None,
    attempt: int,
    backoff_seconds: float,
    max_retry_delay_seconds: float,
) -> float:
    """Return retry delay from Retry-After header or exponential fallback."""
    if isinstance(retry_after, str) and retry_after.strip():
        retry_after_value = retry_after.strip()
        if retry_after_value.isdigit():
            return _cap_retry_delay_seconds(float(retry_after_value), max_retry_delay_seconds)
        try:
            retry_dt = parsedate_to_datetime(retry_after_value)
            delay = max(0.0, retry_dt.timestamp() - time.time())
            return _cap_retry_delay_seconds(delay, max_retry_delay_seconds)
        except (TypeError, ValueError):
            pass

    return _cap_retry_delay_seconds(backoff_seconds * (2 ** (attempt - 1)), max_retry_delay_seconds)


def _cap_retry_delay_seconds(delay_seconds: float, max_retry_delay_seconds: float) -> float:
    """Cap retry delay and log if a source-provided value exceeds the configured maximum."""
    delay_seconds = max(0.0, delay_seconds)
    if max_retry_delay_seconds < 0:
        return delay_seconds
    if delay_seconds > max_retry_delay_seconds:
        LOGGER.warning(
            "Retry delay %.2fs exceeds max %.2fs; capping delay",
            delay_seconds,
            max_retry_delay_seconds,
        )
        return max_retry_delay_seconds
    return delay_seconds


def _close_response_quietly(response: requests.Response | None) -> None:
    """Close an HTTP response while suppressing close-time errors."""
    if response is None:
        return
    try:
        response.close()
    except requests.RequestException:
        return


def append_error(errors: list[str] | None, message: str) -> None:
    """Append a unique error message to a mutable collector."""
    if errors is None:
        return
    if message not in errors:
        errors.append(message)


def strip_jats_xml(xml_abstract: str) -> str:
    """Strip JATS/XML tags from text and unescape HTML entities."""
    try:
        root = ET.fromstring(f"<root>{xml_abstract}</root>")
        text = ET.tostring(root, encoding="unicode", method="text")
        return html.unescape(text).strip()
    except ET.ParseError:
        pass

    text = re.sub(r"<[^>]+>", "", xml_abstract)
    return html.unescape(text).strip()


def clean_text(text: str) -> str:
    """Normalize text by stripping markup/entities and collapsing whitespace."""
    cleaned = strip_jats_xml(text)
    cleaned = re.sub(r"\s+", " ", html.unescape(cleaned)).strip()
    return cleaned


def find_first_text(payload: object, keys: tuple[str, ...]) -> tuple[str, str] | None:
    """Recursively find the first non-empty string value for target keys."""
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return key, value
        for value in payload.values():
            found = find_first_text(value, keys)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = find_first_text(item, keys)
            if found is not None:
                return found
    return None


def extract_first_xml_text(root: ET.Element, tags: set[str]) -> str | None:
    """Extract text from the first XML element whose localname is in tags."""
    for element in root.iter():
        if _local_name(element.tag) not in tags:
            continue
        raw = "".join(element.itertext())
        cleaned = clean_text(raw)
        if cleaned:
            return cleaned
    return None


def text_mentions_doi(text: str, requested_doi: str) -> bool:
    """Return True when text contains an exact DOI reference match."""
    requested_normalized = normalize_doi(requested_doi).lower()
    for match in DOI_REFERENCE_PATTERN.finditer(text):
        candidate = _strip_trailing_doi_delimiters(match.group(0))
        if normalize_doi(candidate).lower() == requested_normalized:
            return True
    return False


def _strip_trailing_doi_delimiters(token: str) -> str:
    """Trim surrounding punctuation that commonly follows DOI references in text."""
    trimmed = token.rstrip(TRAILING_DOI_DELIMITERS)
    while trimmed.endswith(")") and trimmed.count(")") > trimmed.count("("):
        trimmed = trimmed[:-1]
    return trimmed


def _local_name(tag: str) -> str:
    """Return XML localname from namespaced tags."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    if ":" in tag:
        return tag.split(":", 1)[1]
    return tag


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
        response = request_with_retry(
            "GET",
            f"{DOI_HANDLE_API}/{doi}",
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        data = response.json()
        code = data.get("responseCode")
        return DoiValidation(
            doi=doi,
            is_valid=code == HANDLE_RESPONSE_SUCCESS,
            handle_response_code=code,
            error=None if code == HANDLE_RESPONSE_SUCCESS else f"Handle API responseCode={code}",
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
        response = request_with_retry(
            "GET",
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
        response = request_with_retry(
            "GET",
            f"{CROSSREF_API_URL}/{doi}",
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
        response = request_with_retry(
            "GET",
            f"{DATACITE_API_URL}/{doi}",
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

def decode_inverted_abstract(inverted_index: dict[str, list[int]]) -> str:
    """Reconstruct plain text from an OpenAlex inverted abstract index.

    OpenAlex stores abstracts as ``{word: [position_0, position_1, ...]}``,
    e.g. ``{"While": [0, 140], "in": [6, 181, 191]}``.  This format is
    optimized for search indexing — you can find which abstracts contain a
    word without reconstructing the full text, and it compresses well since
    common words aren't stored repeatedly.

    We rebuild the original text by placing each word at its position(s) and
    joining with spaces.  This is technically lossy: punctuation attached to
    words (``"soil,"`` at position 82) survives, but any non-space whitespace
    or formatting in the original is lost.  That's why ``SourceRetrievalResult``
    exposes both ``abstract`` (reconstructed) and ``raw_abstract`` (the
    original JSON-serialized inverted index).

    Args:
        inverted_index: Mapping of word -> list of integer positions.

    Returns:
        Reconstructed abstract as a single string.
    """
    words: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word
    if not words:
        return ""
    max_pos = max(words)
    return " ".join(words.get(i, "") for i in range(max_pos + 1))


def infer_provider_from_doi(doi: str) -> str | None:
    """Infer a provider key from DOI prefix mapping."""
    TARGET_PROVIDER_PREFIXES: dict[str, str] = {
        "10.6073": "edi",
        "10.46936": "emsl",
        "10.15485": "ess-dive",
        "10.21952": "ess-dive",
        "10.6084": "figshare",
        "10.25585": "jgi",
        "10.25982": "kbase",
        "10.25345": "massive",
        "10.17504": "cyverse",
        "10.5281": "zenodo",
    }

    prefix = doi.split("/", 1)[0] if "/" in doi else ""
    return TARGET_PROVIDER_PREFIXES.get(prefix)


def infer_provider_from_text(text: str | None) -> str | None:
    """Infer provider from publisher/source text keywords."""
    TARGET_PROVIDER_KEYWORDS: dict[str, str] = {
        "environmental data initiative": "edi",
        "emsl": "emsl",
        "ess-dive": "ess-dive",
        "figshare": "figshare",
        "genomic standards consortium": "gsc",
        "jgi": "jgi",
        "kbase": "kbase",
        "massive": "massive",
        "zenodo": "zenodo",
        "cyverse": "cyverse",
    }
    if not text:
        return None
    lowered = text.lower()
    for key, provider in TARGET_PROVIDER_KEYWORDS.items():
        if key in lowered:
            return provider
    return None
