"""EDI DOI resolver."""

import xml.etree.ElementTree as ET

import requests

from nmdc_metadata_suggestor.constants import (
    DEFAULT_TIMEOUT,
    EDI_DOI_API,
    MAX_EDI_METADATA_XML_CHARS,
    UNSAFE_XML_DECLARATION_PATTERN,
    USER_AGENT,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    extract_first_xml_text,
    request_with_retry,
)
from nmdc_metadata_suggestor.doi_ingestion.resolver_context import ResolverContext


def try_edi(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return context from EDI PASTA metadata resolved by DOI."""
    metadata_url = _resolve_edi_metadata_url(doi, errors=errors)
    if metadata_url is None:
        return None

    try:
        response = request_with_retry(
            "GET",
            metadata_url,
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"EDI metadata request returned HTTP {response.status_code}")
            return None
        xml_text = response.text
        if len(xml_text) > MAX_EDI_METADATA_XML_CHARS:
            append_error(errors, "EDI metadata XML exceeded size limit")
            return None
        if UNSAFE_XML_DECLARATION_PATTERN.search(xml_text):
            append_error(errors, "EDI metadata XML contains unsafe declarations")
            return None
        root = ET.fromstring(xml_text)
    except requests.RequestException as exc:
        append_error(errors, f"EDI metadata request failed: {exc.__class__.__name__}")
        return None
    except ET.ParseError:
        append_error(errors, "EDI metadata response was not valid XML")
        return None

    abstract = extract_first_xml_text(root, {"abstract"})
    if abstract:
        return ResolverContext(text=abstract, raw_text=abstract, kind="abstract")

    description = extract_first_xml_text(root, {"purpose", "description", "title"})
    if description:
        return ResolverContext(text=description, raw_text=description, kind="description")
    append_error(errors, "EDI metadata contained no abstract/description text")
    return None


def _resolve_edi_metadata_url(doi: str, errors: list[str] | None = None) -> str | None:
    """Resolve EDI metadata URL from an EDI DOI."""
    try:
        response = request_with_retry(
            "GET",
            f"{EDI_DOI_API}/doi:{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"EDI DOI API returned HTTP {response.status_code}")
            return None
    except requests.RequestException as exc:
        append_error(errors, f"EDI DOI API request failed: {exc.__class__.__name__}")
        return None

    for line in response.text.splitlines():
        candidate = line.strip()
        if "/package/metadata/eml/" in candidate:
            return candidate
    append_error(errors, "EDI DOI API response did not include metadata URL")
    return None
