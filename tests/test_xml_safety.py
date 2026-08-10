"""Tests for the shared untrusted-XML parsing guards."""

from nmdc_metadata_suggestor_ai_tool.xml_safety import parse_untrusted_xml

BILLION_LAUGHS = (
    '<?xml version="1.0"?>'
    '<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>'
    "<article>&lol2;</article>"
)


def test_parses_well_formed_xml() -> None:
    root, reason = parse_untrusted_xml("<doc><a>hi</a></doc>")
    assert reason is None
    assert root is not None
    assert root.findtext("a") == "hi"


def test_accepts_utf8_bytes() -> None:
    root, reason = parse_untrusted_xml("<doc>café</doc>".encode())
    assert reason is None
    assert root is not None
    assert root.text == "café"


def test_rejects_non_utf8_bytes() -> None:
    root, reason = parse_untrusted_xml(b"<doc>\xff\xfe</doc>")
    assert root is None
    assert reason == "XML was not valid UTF-8"


def test_rejects_non_text_input() -> None:
    root, reason = parse_untrusted_xml(None)  # type: ignore[arg-type]
    assert root is None
    assert reason == "response was not XML text"


def test_rejects_entity_declarations() -> None:
    # Every caller relies on this: ElementTree expands internal entities, so a
    # billion-laughs payload must be refused before it reaches the parser.
    root, reason = parse_untrusted_xml(BILLION_LAUGHS)
    assert root is None
    assert reason == "XML contains unsafe declarations"


def test_rejects_doctype_regardless_of_spacing_and_case() -> None:
    for payload in ("<! doctype doc><doc/>", "<!DocType doc><doc/>", "<!ENTITY x 'y'><doc/>"):
        root, reason = parse_untrusted_xml(payload)
        assert root is None, payload
        assert reason == "XML contains unsafe declarations"


def test_rejects_oversized_payload_before_parsing() -> None:
    root, reason = parse_untrusted_xml("<doc/>", max_chars=3)
    assert root is None
    assert reason == "XML exceeded size limit"


def test_no_size_limit_by_default() -> None:
    root, reason = parse_untrusted_xml("<doc>" + "x" * 10_000 + "</doc>")
    assert reason is None
    assert root is not None


def test_reports_malformed_xml() -> None:
    root, reason = parse_untrusted_xml("<not-closed>")
    assert root is None
    assert reason == "response was not valid XML"
