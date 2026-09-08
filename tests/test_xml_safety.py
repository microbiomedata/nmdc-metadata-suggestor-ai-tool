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
    assert root is None, reason


def test_rejects_malformed_declarations_and_stray_entities() -> None:
    """Renamed from test_rejects_doctype_regardless_of_spacing_and_case.

    It no longer describes the behaviour: a well-formed DOCTYPE carrying no
    entities is now allowed, because JATS uses one. These three payloads are
    still refused, but as malformed XML rather than as unsafe declarations, so
    the assertion is on the refusal rather than on the reason string.
    """
    for payload in ("<! doctype doc><doc/>", "<!DocType doc><doc/>", "<!ENTITY x 'y'><doc/>"):
        root, reason = parse_untrusted_xml(payload)
        assert root is None, f"{payload} was accepted"
        assert reason is not None


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
    assert root is None, reason

    root, reason = parse_untrusted_xml(_billion_laughs())
    assert root is None, reason


# --- CDATA holds character data, so a declaration inside one is text ---


def test_cdata_quoting_a_doctype_round_trips() -> None:
    """An abstract may legitimately quote markup, and must survive intact.

    The previous regex guard refused this and degraded to a tag-stripping
    fallback that returned "text]]>". Guarding at the parser removes the
    false positive without weakening anything.
    """
    from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import strip_jats_xml

    assert strip_jats_xml("<p><![CDATA[Example <!DOCTYPE x> text]]></p>") == (
        "Example <!DOCTYPE x> text"
    )


def test_document_type_declaration_alone_is_allowed() -> None:
    """A DOCTYPE without entities is normal in JATS and must not be refused.

    Three of four Europe PMC full texts sampled on 2026-08-24 carried
    ``<!DOCTYPE article``. The regex guard refused all three, so supplement
    caption extraction silently returned nothing for them.
    """
    root, reason = parse_untrusted_xml("<!DOCTYPE article><root>hi</root>")
    assert root is not None, reason
    assert root.text == "hi"


def test_regex_lexing_cannot_be_made_safe() -> None:
    """Why the scan does not try to skip CDATA.

    A comment can open inside a CDATA marker and close after a real DOCTYPE, so
    any regex that treats comments and CDATA as independent regions can be made
    to blank out a live declaration while ElementTree still parses and expands
    it. Verified: blanking CDATA alone let this payload through and &e; expanded.
    """
    payload = (
        '<!-- <![CDATA[ --><!DOCTYPE root [<!ENTITY e "expanded">]><root>&e;</root><!-- ]]> -->'
    )
    root, reason = parse_untrusted_xml(payload)
    assert root is None, "a declaration hidden across lexical regions must still be refused"
    assert reason == "XML contains unsafe declarations"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            '<![CDATA[]]><!DOCTYPE x [<!ENTITY e "P">]><root>&e;</root>', id="cdata-then-doctype"
        ),
        pytest.param(
            '<![CDATA[ <!DOCTYPE x [<!ENTITY e "P">]><root>&e;</root>', id="unterminated-cdata"
        ),
        pytest.param(
            '<!DOCTYPE x [<!ENTITY e "<![CDATA[">]><root>&e;</root>', id="doctype-wrapping-cdata"
        ),
        pytest.param(
            '<![CDATA[a]]><!DOCTYPE x [<!ENTITY e "P">]><![CDATA[b]]><root>&e;</root>',
            id="declaration-between-cdata-sections",
        ),
        pytest.param('<![CDATA[]]><!doctype x [<!entity e "P">]><root>&e;</root>', id="lowercase"),
    ],
)
def test_blanking_cdata_cannot_hide_a_real_declaration(payload: str) -> None:
    """A real DOCTYPE precedes the root element, so it can never sit inside CDATA."""
    root, reason = parse_untrusted_xml(payload)
    assert root is None, reason


# --- the size limit must stay wired into the caller, not just exist in the helper ---


def test_supplement_caption_parsing_enforces_the_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins ``max_chars`` into the ``parse_supplement_captions`` call.

    Without this the limit can be deleted from the call site and the whole
    suite still passes, because every other test uses documents far under it.
    The order matters: assert the fixture parses to a caption first, otherwise
    a document that yields nothing for unrelated reasons would satisfy the
    second assertion whether or not the limit is applied.
    """
    from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements import shared

    jats = (
        "<article><body>"
        '<supplementary-material xlink:href="table1.csv" '
        'xmlns:xlink="http://www.w3.org/1999/xlink">'
        "<caption><p>Supplementary table one</p></caption>"
        "</supplementary-material></body></article>"
    )

    assert shared.parse_supplement_captions(jats) == {"table1": "Supplementary table one"}, (
        "the fixture must parse to a caption first, or the assertion below proves nothing"
    )

    monkeypatch.setattr(shared, "MAX_EUROPEPMC_FULLTEXT_XML_CHARS", 10)
    assert not shared.parse_supplement_captions(jats)


def test_real_jats_doctype_is_accepted() -> None:
    """Regression for a live bug: JATS carries a DOCTYPE and we refused it.

    Sampled four Europe PMC full texts on 2026-08-24. Three began with
    ``<!DOCTYPE article ...``, and the regex guard refused all three, so
    ``parse_supplement_captions`` returned nothing for them. PMC9950430 alone
    holds 23 supplement elements and now yields 12 captions.

    The declaration below is the real one from PMC9950430, trimmed to its
    public and system identifiers.
    """
    doctype = (
        '<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) '
        'Journal Archiving and Interchange DTD v1.2 20190208//EN" '
        '"JATS-archivearticle1.dtd">'
    )
    root, reason = parse_untrusted_xml(f"{doctype}<article><body><p>hi</p></body></article>")
    assert root is not None, f"real JATS must parse, got {reason!r}"
    assert root.findtext("./body/p") == "hi"


def test_external_dtd_reference_is_not_fetched() -> None:
    """The JATS DOCTYPE names a system DTD; it must never be retrieved."""
    doctype = '<!DOCTYPE article SYSTEM "http://127.0.0.1:9/nonexistent.dtd">'
    root, reason = parse_untrusted_xml(f"{doctype}<article>hi</article>")
    assert root is not None, reason
    assert root.text == "hi"
