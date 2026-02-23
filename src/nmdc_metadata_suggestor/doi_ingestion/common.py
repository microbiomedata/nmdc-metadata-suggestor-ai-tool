"""Shared parsing helpers for DOI ingestion source resolvers."""

import html
import re
import xml.etree.ElementTree as ET

from nmdc_metadata_suggestor.publication_ingestion.abstract_retriever import strip_jats_xml
from nmdc_metadata_suggestor.publication_ingestion.doi_utils import normalize_doi

DOI_REFERENCE_PATTERN = re.compile(
    r"(?:https?://doi\.org/|doi:)?10\.\d{4,9}/\S+",
    re.IGNORECASE,
)
TRAILING_DOI_DELIMITERS = ".,;:]}>\"'"


def append_error(errors: list[str] | None, message: str) -> None:
    """Append a unique error message to a mutable collector."""
    if errors is None:
        return
    if message not in errors:
        errors.append(message)


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
