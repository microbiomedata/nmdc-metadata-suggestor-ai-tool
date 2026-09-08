"""Guarded parsing of XML fetched from external services.

Every payload this package parses comes off the network, so all of it goes
through :func:`parse_untrusted_xml` rather than calling ``ElementTree`` directly.
Keeping the guards in one place means a future hardening change lands for every
caller at once.

The guard is ``defusedxml``, which refuses entity declarations and external
references inside the parser. It replaced a regex that scanned for
``<!DOCTYPE``/``<!ENTITY`` before parsing, for two reasons, both measured.

The regex was unsafe. XML comments, CDATA sections and processing instructions
are lexical regions a regex cannot track across, so a comment could open inside
a CDATA marker and close after a real declaration, hiding it from the scan while
ElementTree still parsed and expanded it.

The regex was also too strict to work. A plain ``<!DOCTYPE article`` carries no
entities and is normal in JATS: three of four Europe PMC full texts sampled on
2026-08-24 had one, and the scan refused all three, so supplement caption
extraction silently returned nothing for them.
"""

import xml.etree.ElementTree as ET

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as defused_fromstring


def parse_untrusted_xml(
    xml_text: str | bytes,
    *,
    max_chars: int | None = None,
) -> tuple[ET.Element | None, str | None]:
    """Parse untrusted XML, refusing payloads that are oversized or unsafe.

    Entity declarations and external references are refused by the parser, which
    blocks entity expansion ("billion laughs") and external-entity reads. A
    document type declaration on its own is allowed, because it is normal in
    JATS and harmless without entities.

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

    try:
        return defused_fromstring(xml_text), None
    except DefusedXmlException:
        return None, "XML contains unsafe declarations"
    except ET.ParseError:
        return None, "response was not valid XML"
