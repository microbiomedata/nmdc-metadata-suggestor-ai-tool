"""Shared parsing helpers for DOI ingestion source resolvers."""

import html
import re
import xml.etree.ElementTree as ET

from nmdc_metadata_suggestor.publication_ingestion.abstract_retriever import strip_jats_xml


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
    """Return True when text appears to reference the requested DOI."""
    lowered = text.lower()
    requested = requested_doi.lower()
    doi_variants = {
        requested,
        f"doi:{requested}",
        f"https://doi.org/{requested}",
        f"http://doi.org/{requested}",
    }
    return any(variant in lowered for variant in doi_variants)


def _local_name(tag: str) -> str:
    """Return XML localname from namespaced tags."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    if ":" in tag:
        return tag.split(":", 1)[1]
    return tag
