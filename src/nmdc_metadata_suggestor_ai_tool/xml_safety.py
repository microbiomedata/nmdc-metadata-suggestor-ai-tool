"""Guarded parsing of XML fetched from external services.

Every payload this package parses comes off the network, so all of it goes
through :func:`parse_untrusted_xml` rather than calling ``ElementTree`` directly.
Keeping the guards in one place means a future hardening change (a stricter
pattern, a different parser) lands for every caller at once.
"""

import xml.etree.ElementTree as ET

from nmdc_metadata_suggestor_ai_tool.constants import (
    CDATA_SECTION_PATTERN,
    UNSAFE_XML_DECLARATION_PATTERN,
)


def parse_untrusted_xml(
    xml_text: str | bytes,
    *,
    max_chars: int | None = None,
) -> tuple[ET.Element | None, str | None]:
    """Parse untrusted XML, refusing payloads that are oversized or unsafe.

    DOCTYPE/ENTITY declarations are rejected outright: ElementTree does not
    expand external entities, but it does expand internal ones, so a declaration
    is the entry point for entity-expansion ("billion laughs") attacks.

    Args:
        xml_text: The document, as text or UTF-8 bytes.
        max_chars: Reject documents longer than this. ``None`` means no limit.

    Returns:
        ``(root, None)`` on success, or ``(None, reason)`` where *reason* is a
        phrase suitable for appending to a caller-specific prefix, e.g.
        ``f"EDI metadata {reason}"``.
    """
    if isinstance(xml_text, bytes):
        try:
            xml_text = xml_text.decode("utf-8")
        except UnicodeDecodeError:
            return None, "XML was not valid UTF-8"
    if not isinstance(xml_text, str):
        return None, "response was not XML text"

    if max_chars is not None and len(xml_text) > max_chars:
        return None, "XML exceeded size limit"
    # Scan with CDATA blanked out: its contents are character data, so a
    # "<!DOCTYPE" there is text an author wrote, not a declaration.
    if UNSAFE_XML_DECLARATION_PATTERN.search(CDATA_SECTION_PATTERN.sub("", xml_text)):
        return None, "XML contains unsafe declarations"

    try:
        return ET.fromstring(xml_text), None
    except ET.ParseError:
        return None, "response was not valid XML"
