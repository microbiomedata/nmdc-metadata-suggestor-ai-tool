"""Tests for the shared untrusted-XML parsing guards."""

from pathlib import Path

import pytest

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


# --- strip_jats_xml: abstracts come off the network, so the same guards apply ---


def _billion_laughs(levels: int = 5) -> str:
    """A bounded entity-expansion payload. Bounded so a regression cannot hang CI."""
    entities = '<!ENTITY a0 "lol">' + "".join(
        f'<!ENTITY a{i} "&a{i - 1};&a{i - 1};&a{i - 1};&a{i - 1};">' for i in range(1, levels + 1)
    )
    return f'<?xml version="1.0"?><!DOCTYPE x [{entities}]><root>&a{levels};</root>'


def test_strip_jats_xml_still_strips_ordinary_markup() -> None:
    from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import strip_jats_xml

    assert strip_jats_xml("<p>Soil <italic>microbial</italic> communities</p>") == (
        "Soil microbial communities"
    )


def test_strip_jats_xml_unescapes_entities() -> None:
    from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import strip_jats_xml

    assert strip_jats_xml("<p>a &amp; b</p>") == "a & b"


def test_strip_jats_xml_does_not_expand_declared_entities() -> None:
    from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import strip_jats_xml

    out = strip_jats_xml(_billion_laughs())
    assert "lollol" not in out
    assert len(out) < 200


def test_strip_jats_xml_does_not_read_local_files(tmp_path: Path) -> None:
    """An external-entity payload must not reach the filesystem."""
    from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import strip_jats_xml

    secret = tmp_path / "secret.txt"
    secret.write_text("SENSITIVE-FILE-CONTENTS")
    payload = (
        '<?xml version="1.0"?>'
        f'<!DOCTYPE x [<!ENTITY xxe SYSTEM "file://{secret}">]>'
        "<root>&xxe;</root>"
    )
    assert "SENSITIVE-FILE-CONTENTS" not in strip_jats_xml(payload)


def test_strip_jats_xml_routes_through_the_shared_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the guard into the call path.

    The wrapping element and the declaration guard each block entity expansion
    on their own, so a test that only feeds in a hostile abstract keeps passing
    when either one is removed. It takes losing both to fail. This asserts the
    guard is actually reached, so dropping it back to a bare ElementTree parse
    is caught on its own.
    """
    from nmdc_metadata_suggestor_ai_tool.doi_ingestion import doi_utils

    seen: list[str] = []
    real = doi_utils.parse_untrusted_xml

    def _spy(xml_text: str | bytes, **kwargs: object) -> object:
        seen.append(str(xml_text))
        return real(xml_text, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(doi_utils, "parse_untrusted_xml", _spy)
    doi_utils.strip_jats_xml("<p>hello</p>")

    assert seen, "strip_jats_xml must parse through parse_untrusted_xml"
    assert seen[0] == "<root><p>hello</p></root>", "the wrapping element is a second layer; keep it"


def test_declaration_guard_rejects_payloads_with_and_without_a_wrapper() -> None:
    """The guard does not depend on how the caller wraps the payload."""
    root, reason = parse_untrusted_xml(f"<root>{_billion_laughs()}</root>")
    assert root is None
    assert reason == "XML contains unsafe declarations"

    root, reason = parse_untrusted_xml(_billion_laughs())
    assert root is None
    assert reason == "XML contains unsafe declarations"
