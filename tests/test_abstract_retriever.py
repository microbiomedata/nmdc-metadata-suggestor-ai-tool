"""Tests for abstract retrieval waterfall.

Unit tests mock all HTTP with ``responses``; integration tests hit real APIs.
"""

import pytest
import responses
from requests.exceptions import ConnectionError as RequestsConnectionError

from nmdc_metadata_suggestor.constants import (
    ALL_SOURCES,
    CROSSREF_API_URL,
    DATACITE_API_URL,
    DOI_RA_API,
    OPENALEX_API_URL,
    OSTI_E2_API_URL,
    PUBMED_EFETCH,
    PUBMED_ID_CONVERTER,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    decode_inverted_abstract,
    strip_jats_xml,
)
from nmdc_metadata_suggestor.doi_ingestion.main import get_doi_description_or_abstract
from nmdc_metadata_suggestor.models.doi import SourceRetrievalResult

integration = pytest.mark.integration

SAMPLE_DOI = "10.1038/s41564-020-00861-0"


# ---------------------------------------------------------------------------
# Helpers for mocking the classification gate
# ---------------------------------------------------------------------------


def _mock_classify_as_publication(doi: str) -> None:
    """Mock RA + Crossref so classify_doi sees a valid publication."""
    responses.add(
        responses.GET,
        f"{DOI_RA_API}/{doi}",
        json=[{"DOI": doi, "RA": "Crossref"}],
    )
    responses.add(
        responses.GET,
        f"https://api.crossref.org/works/{doi}",
        json={"message": {"type": "journal-article", "publisher": "Test"}},
    )


def _mock_classify_as_dataset(doi: str) -> None:
    """Mock RA + Crossref so classify_doi sees a dataset."""
    responses.add(
        responses.GET,
        f"{DOI_RA_API}/{doi}",
        json=[{"DOI": doi, "RA": "Crossref"}],
    )
    responses.add(
        responses.GET,
        f"https://api.crossref.org/works/{doi}",
        json={"message": {"type": "dataset", "publisher": "Test"}},
    )


def _mock_classify_as_datacite_software(doi: str) -> None:
    """Mock RA + DataCite so classify_doi sees Software (unmapped NMDC category)."""
    responses.add(
        responses.GET,
        f"{DOI_RA_API}/{doi}",
        json=[{"DOI": doi, "RA": "DataCite"}],
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API_URL}/{doi}",
        json={
            "data": {
                "attributes": {
                    "types": {
                        "resourceType": "Software",
                        "resourceTypeGeneral": "Software",
                    },
                    "publisher": "Zenodo",
                }
            }
        },
    )


def _mock_classify_as_crossref_component(doi: str) -> None:
    """Mock RA + Crossref so classify_doi sees a component (figure/table)."""
    responses.add(
        responses.GET,
        f"{DOI_RA_API}/{doi}",
        json=[{"DOI": doi, "RA": "Crossref"}],
    )
    responses.add(
        responses.GET,
        f"https://api.crossref.org/works/{doi}",
        json={"message": {"type": "component", "publisher": "Test"}},
    )


# ---------------------------------------------------------------------------
# Parsing helpers — pure functions, no mocking
# ---------------------------------------------------------------------------


class TestDecodeInvertedAbstract:
    def test_basic(self) -> None:
        inverted = {"Hello": [0], "world": [1], "this": [2], "works": [3]}
        assert decode_inverted_abstract(inverted) == "Hello world this works"

    def test_repeated_words(self) -> None:
        inverted = {"the": [0, 2], "cat": [1], "sat": [3]}
        assert decode_inverted_abstract(inverted) == "the cat the sat"

    def test_empty(self) -> None:
        assert decode_inverted_abstract({}) == ""

    def test_gap_in_positions(self) -> None:
        """Missing positions produce empty strings that join cleanly."""
        inverted = {"Hello": [0], "world": [5]}
        result = decode_inverted_abstract(inverted)
        assert result.startswith("Hello")
        assert result.endswith("world")


class TestStripJatsXml:
    def test_simple_jats(self) -> None:
        xml = "<jats:p>This is an abstract.</jats:p>"
        assert strip_jats_xml(xml) == "This is an abstract."

    def test_nested_jats(self) -> None:
        xml = "<jats:p>Contains <jats:italic>italic</jats:italic> text.</jats:p>"
        assert strip_jats_xml(xml) == "Contains italic text."

    def test_html_entities(self) -> None:
        xml = "<jats:p>CO&amp;O &lt;values&gt;</jats:p>"
        assert strip_jats_xml(xml) == "CO&O <values>"

    def test_plain_text_passthrough(self) -> None:
        text = "Already plain text, no XML here."
        assert strip_jats_xml(text) == text

    def test_unicode_entities(self) -> None:
        xml = "<jats:p>don&#x2019;t stop</jats:p>"
        assert strip_jats_xml(xml) == "don\u2019t stop"


# ---------------------------------------------------------------------------
# get_abstract — waterfall tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestGetAbstractClassificationGate:
    """Test that invalid and non-publication DOIs are refused."""

    def test_invalid_doi_refused(self) -> None:
        result = get_doi_description_or_abstract("not-a-doi")
        assert result.context is None
        assert result.error is not None
        assert "Invalid DOI" in result.error

    @responses.activate
    def test_dataset_doi_refused(self) -> None:
        doi = "10.15485/1729719"
        _mock_classify_as_dataset(doi)
        result = get_doi_description_or_abstract(doi)
        assert result.context is None
        assert "not a publication" in result.error

    @responses.activate
    def test_datacite_software_refused(self) -> None:
        """DataCite Software DOI — unmapped NMDC category but clearly not a publication."""
        doi = "10.5281/zenodo.1234567"
        _mock_classify_as_datacite_software(doi)
        result = get_doi_description_or_abstract(doi)
        assert result.context is None
        assert "Software" in result.error
        assert "not a publication type" in result.error

    @responses.activate
    def test_crossref_component_refused(self) -> None:
        """Crossref component (figure/table) — not a publication."""
        doi = "10.1038/s41564-020-00861-0.fig1"
        _mock_classify_as_crossref_component(doi)
        result = get_doi_description_or_abstract(doi)
        assert result.context is None
        assert "component" in result.error
        assert "not a publication type" in result.error

    @responses.activate
    def test_skip_classification_bypasses_gate(self) -> None:
        """skip_classification=True goes straight to waterfall."""
        doi = SAMPLE_DOI
        # Mock all sources in the new default waterfall order (no classification mocks)
        responses.add(responses.GET, f"{DATACITE_API_URL}/{doi}", status=404)
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        responses.add(responses.GET, f"https://doi.org/{doi}", status=404)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(responses.GET, PUBMED_ID_CONVERTER, json={"records": []})
        result = get_doi_description_or_abstract(doi, skip_classification=True)
        assert result.error == "No description or abstract found in any source"


class TestGetAbstractWaterfall:
    """Test waterfall ordering — each test mocks classification + sources."""

    @responses.activate
    def test_osti_hit(self) -> None:
        """OSTI source returns description for an OSTI DOI."""
        doi = "10.15485/1729719"
        _mock_classify_as_publication(doi)
        responses.add(
            responses.GET,
            OSTI_E2_API_URL,
            json=[{"doi": doi, "description": "OSTI abstract text."}],
        )

        result = get_doi_description_or_abstract(doi, sources=["osti"])
        assert result.context == "OSTI abstract text."
        assert result.raw_context == "OSTI abstract text."
        assert result.source == "osti"
        assert result.attempts == ["osti"]

    @responses.activate
    def test_openalex_hit(self) -> None:
        """OpenAlex returns abstract, waterfall stops immediately."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(
            responses.GET,
            f"{OPENALEX_API_URL}/https://doi.org/{doi}",
            json={"abstract_inverted_index": {"Hello": [0], "world": [1]}},
        )
        result = get_doi_description_or_abstract(doi, sources=["openalex"])
        assert result.context == "Hello world"
        assert result.raw_context == '{"Hello": [0], "world": [1]}'
        assert result.source == "openalex"
        assert result.context_type == "inverted_index"

    @responses.activate
    def test_openalex_miss_crossref_hit(self) -> None:
        """OpenAlex has no abstract, Crossref does."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        # OpenAlex miss
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        # Crossref hit
        responses.add(
            responses.GET,
            f"{CROSSREF_API_URL}/{doi}",
            json={"message": {"abstract": "<jats:p>Crossref abstract.</jats:p>"}},
        )
        result = get_doi_description_or_abstract(doi, sources=["openalex", "crossref"])
        assert result.context == "Crossref abstract."
        assert result.raw_context == "<jats:p>Crossref abstract.</jats:p>"
        assert result.source == "crossref"
        assert result.context_type == "jats_xml"

    @responses.activate
    def test_pubmed_fallback(self) -> None:
        """OpenAlex + Crossref miss, PubMed has it."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        # OpenAlex miss
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        # Crossref miss
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        # PubMed ID converter
        responses.add(
            responses.GET,
            PUBMED_ID_CONVERTER,
            json={"records": [{"pmid": "12345678"}]},
        )
        # PubMed efetch
        responses.add(responses.GET, PUBMED_EFETCH, body="PubMed abstract text here.")
        result = get_doi_description_or_abstract(doi, sources=["openalex", "crossref", "pubmed"])
        assert result.context == "PubMed abstract text here."
        assert result.raw_context == "PubMed abstract text here."
        assert result.source == "pubmed"

    @responses.activate
    def test_content_negotiation_fallback(self) -> None:
        """OpenAlex + Crossref + PubMed miss, content negotiation provides the abstract."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        # OpenAlex miss
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        # Crossref miss
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        # PubMed miss
        responses.add(responses.GET, PUBMED_ID_CONVERTER, json={"records": []})
        # Content negotiation hit
        responses.add(
            responses.GET,
            f"https://doi.org/{doi}",
            json={"abstract": "Content negotiation abstract."},
        )
        result = get_doi_description_or_abstract(
            doi, sources=["openalex", "crossref", "pubmed", "content_negotiation"]
        )
        assert result.context == "Content negotiation abstract."
        assert result.raw_context == "Content negotiation abstract."
        assert result.source == "content_negotiation"

    @responses.activate
    def test_content_negotiation_note_fallback(self) -> None:
        """Content negotiation note field is used as fallback when no abstract."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        responses.add(responses.GET, PUBMED_ID_CONVERTER, json={"records": []})
        responses.add(
            responses.GET,
            f"https://doi.org/{doi}",
            json={"note": "Context note only, no abstract"},
        )
        result = get_doi_description_or_abstract(
            doi, sources=["openalex", "crossref", "pubmed", "content_negotiation"]
        )
        # Content negotiation now uses note as a fallback description
        assert result.context == "Context note only, no abstract"
        assert result.source == "content_negotiation"

    @responses.activate
    def test_all_miss(self) -> None:
        """All sources return nothing — graceful None."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        responses.add(responses.GET, PUBMED_ID_CONVERTER, json={"records": []})
        responses.add(responses.GET, f"https://doi.org/{doi}", json={})
        result = get_doi_description_or_abstract(
            doi, sources=["openalex", "crossref", "pubmed", "content_negotiation"]
        )
        assert result.context is None
        assert result.error == "No description or abstract found in any source"

    @responses.activate
    def test_network_errors_waterfall_continues(self) -> None:
        """Each source errors, waterfall continues to next."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(
            responses.GET,
            f"{OPENALEX_API_URL}/https://doi.org/{doi}",
            body=RequestsConnectionError("down"),
        )
        responses.add(
            responses.GET,
            f"{CROSSREF_API_URL}/{doi}",
            body=RequestsConnectionError("down"),
        )
        responses.add(
            responses.GET,
            PUBMED_ID_CONVERTER,
            body=RequestsConnectionError("down"),
        )
        responses.add(
            responses.GET,
            f"https://doi.org/{doi}",
            body=RequestsConnectionError("down"),
        )
        result = get_doi_description_or_abstract(
            doi, sources=["openalex", "crossref", "pubmed", "content_negotiation"]
        )
        assert result.context is None
        assert result.error == "No description or abstract found in any source"

    @responses.activate
    def test_source_recorded_on_hit(self) -> None:
        """The source field records which source returned a result."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(
            responses.GET,
            f"{CROSSREF_API_URL}/{doi}",
            json={"message": {"abstract": "<jats:p>Found it.</jats:p>"}},
        )
        result = get_doi_description_or_abstract(doi, sources=["openalex", "crossref"])
        assert result.source == "crossref"
        assert result.context == "Found it."


class TestGetAbstractResult:
    """Test the SourceRetrievalResult model."""

    def test_defaults(self) -> None:
        r = SourceRetrievalResult(doi="10.1234/test")
        assert r.context is None
        assert r.raw_context is None
        assert r.source is None
        assert r.context_type is None
        assert r.pmid is None
        assert r.provider is None
        assert r.source_errors == {}
        assert r.error is None


class TestContentFormatClassification:
    """Test that content_format correctly identifies the raw response format."""

    @responses.activate
    def test_crossref_plain_text_format(self) -> None:
        """Crossref abstract without XML tags → plain_text, raw == content."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(
            responses.GET,
            f"{CROSSREF_API_URL}/{doi}",
            json={"message": {"abstract": "A plain text abstract with no XML."}},
        )
        result = get_doi_description_or_abstract(doi, sources=["openalex", "crossref"])
        assert result.context_type == "plain_text"
        assert result.context == "A plain text abstract with no XML."
        assert result.raw_context == result.context

    @responses.activate
    def test_crossref_jats_raw_preserved(self) -> None:
        """Crossref JATS → content is stripped but raw_content has original XML."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        jats = "<jats:p>An <jats:italic>important</jats:italic> finding.</jats:p>"
        responses.add(
            responses.GET,
            f"{CROSSREF_API_URL}/{doi}",
            json={"message": {"abstract": jats}},
        )
        result = get_doi_description_or_abstract(doi, sources=["openalex", "crossref"])
        assert result.context == "An important finding."
        assert result.raw_context == jats

    @responses.activate
    def test_content_negotiation_jats_raw_preserved(self) -> None:
        """Content negotiation JATS → raw_content has original tags."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        responses.add(responses.GET, PUBMED_ID_CONVERTER, json={"records": []})
        jats = "<jats:p>Tagged abstract.</jats:p>"
        responses.add(
            responses.GET,
            f"https://doi.org/{doi}",
            json={"abstract": jats},
        )
        result = get_doi_description_or_abstract(
            doi, sources=["openalex", "crossref", "pubmed", "content_negotiation"]
        )
        assert result.context_type == "abstract"
        assert result.context == "Tagged abstract."
        assert result.raw_context == jats

    @responses.activate
    def test_openalex_raw_is_json(self) -> None:
        """OpenAlex raw_content is the JSON-serialized inverted index."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        inverted = {"Hello": [0], "world": [1]}
        responses.add(
            responses.GET,
            f"{OPENALEX_API_URL}/https://doi.org/{doi}",
            json={"abstract_inverted_index": inverted},
        )
        result = get_doi_description_or_abstract(doi, sources=["openalex"])
        assert result.context == "Hello world"
        import json

        assert json.loads(result.raw_context) == inverted


# ---------------------------------------------------------------------------
# Source selection — sources= parameter
# ---------------------------------------------------------------------------


class TestSourceSelection:
    """Test the sources= parameter for selecting/reordering sources."""

    @responses.activate
    def test_single_source_crossref(self) -> None:
        """Only try Crossref, skip OpenAlex entirely."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(
            responses.GET,
            f"{CROSSREF_API_URL}/{doi}",
            json={"message": {"abstract": "<jats:p>Crossref only.</jats:p>"}},
        )
        result = get_doi_description_or_abstract(doi, sources=["crossref"])
        assert result.context == "Crossref only."
        assert result.source == "crossref"

    @responses.activate
    def test_single_source_pubmed(self) -> None:
        """Only try PubMed."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(
            responses.GET,
            PUBMED_ID_CONVERTER,
            json={"records": [{"pmid": "99999"}]},
        )
        responses.add(responses.GET, PUBMED_EFETCH, body="PubMed only text.")
        result = get_doi_description_or_abstract(doi, sources=["pubmed"])
        assert result.context == "PubMed only text."
        assert result.source == "pubmed"

    @responses.activate
    def test_reversed_order(self) -> None:
        """Crossref before OpenAlex — Crossref hits, OpenAlex never called."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(
            responses.GET,
            f"{CROSSREF_API_URL}/{doi}",
            json={"message": {"abstract": "Crossref first."}},
        )
        # OpenAlex is mocked but should never be reached
        responses.add(
            responses.GET,
            f"{OPENALEX_API_URL}/https://doi.org/{doi}",
            json={"abstract_inverted_index": {"Should": [0], "not": [1], "reach": [2]}},
        )
        result = get_doi_description_or_abstract(doi, sources=["crossref", "openalex"])
        assert result.source == "crossref"
        assert result.context == "Crossref first."

    @responses.activate
    def test_subset_miss_all(self) -> None:
        """Two sources tried, both miss — error returned."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        result = get_doi_description_or_abstract(doi, sources=["openalex", "crossref"])
        assert result.context is None
        assert result.error == "No description or abstract found in any source"

    @responses.activate
    def test_invalid_source_ignored(self) -> None:
        """Unknown source names are silently skipped."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(
            responses.GET,
            f"{CROSSREF_API_URL}/{doi}",
            json={"message": {"abstract": "Found it."}},
        )
        result = get_doi_description_or_abstract(doi, sources=["bogus", "crossref"])
        assert result.context == "Found it."
        assert result.source == "crossref"

    def test_all_sources_constant(self) -> None:
        """ALL_SOURCES constant lists the canonical publication abstract sources."""
        assert set(ALL_SOURCES) == {"openalex", "crossref", "pubmed", "content_negotiation", "osti"}

    @responses.activate
    def test_default_source_order(self) -> None:
        """Default waterfall tries datacite, crossref, content_negotiation, openalex, pubmed."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        # Mock all sources in the default order — all miss
        responses.add(responses.GET, f"{DATACITE_API_URL}/{doi}", status=404)
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        responses.add(responses.GET, f"https://doi.org/{doi}", json={})
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(responses.GET, PUBMED_ID_CONVERTER, json={"records": []})
        result = get_doi_description_or_abstract(doi)
        assert result.context is None
        assert result.error == "No description or abstract found in any source"


# ---------------------------------------------------------------------------
# Integration tests — hit real APIs, skip in CI
# ---------------------------------------------------------------------------


# -- Issue #1597 target prefixes --
# Each test covers one of the 6 assigned prefixes from microbiomedata/issues#1597.
# DOIs are taken from the curated fixture (tests/fixtures/doi_test_cases.json).


@integration
def test_get_abstract_real_aslo_doi() -> None:
    """10.4319 — ASLO (limnology/oceanography) journal article."""
    result = get_doi_description_or_abstract("10.4319/lom.2008.6.230", skip_classification=True)
    assert result.context is not None, f"No content found: {result.error}"
    assert len(result.context) > 50
    assert result.source is not None
    assert result.raw_context is not None
    assert result.context_type is not None


@integration
def test_get_abstract_real_nature_doi() -> None:
    """10.1038 — Nature journal article."""
    result = get_doi_description_or_abstract("10.1038/s41564-020-00861-0")
    assert result.context is not None, f"No content found: {result.error}"
    assert len(result.context) > 100
    assert result.raw_context is not None
    assert result.context_type is not None


@integration
def test_get_abstract_real_frontiers_doi() -> None:
    """10.3389 — Frontiers journal article."""
    result = get_doi_description_or_abstract("10.3389/fsoil.2023.1120425")
    assert result.context is not None, f"No content found: {result.error}"
    assert len(result.context) > 100
    assert result.source in ("openalex", "crossref", "datacite")
    assert result.raw_context is not None


@integration
def test_get_abstract_real_elsevier_doi() -> None:
    """10.1016 — Elsevier journal article.

    Elsevier does NOT share abstracts via Crossref, and many Elsevier articles
    are not in PMC. The waterfall may fail for this publisher. This test
    verifies graceful handling: either content is found or the failure is clean.
    """
    result = get_doi_description_or_abstract("10.1016/j.apsoil.2025.106110")
    if result.context:
        assert result.raw_context is not None
        assert result.context_type is not None
    else:
        # Known Elsevier holdout — all sources tried, clean failure
        assert result.error == "No description or abstract found in any source"


@integration
def test_get_abstract_real_soil_science_doi() -> None:
    """10.2136 — Soil Science Society of America book chapter."""
    result = get_doi_description_or_abstract("10.2136/sssabookser5.3.c16")
    # Book chapters may or may not have content; verify graceful handling
    assert result.error is None or "No description or abstract found" in result.error
    if result.context:
        assert result.raw_context is not None
        assert result.context_type is not None


@integration
def test_get_abstract_real_protocols_io_doi() -> None:
    """10.17504 — protocols.io (DataCite). Not a standard publication, NMDC category unmapped."""
    result = get_doi_description_or_abstract("10.17504/protocols.io.kxygxyydkl8j/v1")
    # Category is unmapped (None), so gate allows it through.
    # protocols.io DOIs typically don't have content in the traditional sources.
    assert result.error is None or "No description or abstract found" in result.error
    # Should NOT be refused as non-publication (category is None, not dataset/award)
    if result.error:
        assert "not a publication" not in result.error


# -- Other integration tests --


@integration
def test_get_abstract_real_dataset_doi() -> None:
    """Dataset DOI — should be gracefully refused (not a publication)."""
    result = get_doi_description_or_abstract("10.15485/1729719")
    assert result.context is None
    assert result.error is not None
    assert "not a publication" in result.error


@integration
def test_get_abstract_raw_vs_cleaned() -> None:
    """Verify raw_content differs from content when JATS XML is present."""
    result = get_doi_description_or_abstract("10.3389/fsoil.2023.1120425")
    assert result.context is not None
    assert result.raw_context is not None
    if result.context_type == "jats_xml":
        assert "<" in result.raw_context
        assert "<" not in result.context
    elif result.context_type == "inverted_index":
        assert "{" in result.raw_context
        assert result.context != result.raw_context
