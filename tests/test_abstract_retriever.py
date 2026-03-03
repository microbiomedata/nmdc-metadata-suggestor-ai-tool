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
    ELSEVIER_API_URL,
    OPENALEX_API_URL,
    PUBMED_EFETCH,
    PUBMED_ID_CONVERTER,
    SPRINGER_NATURE_META_API_URL,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import strip_jats_xml
from nmdc_metadata_suggestor.models.doi import SourceRetrievalResult
from nmdc_metadata_suggestor.publication_ingestion.abstract_retriever import (
    decode_inverted_abstract,
    get_abstract,
)

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
        result = get_abstract("not-a-doi")
        assert result.abstract is None
        assert result.error is not None
        assert "Invalid DOI" in result.error
        assert result.attempts == []

    @responses.activate
    def test_dataset_doi_refused(self) -> None:
        doi = "10.15485/1729719"
        _mock_classify_as_dataset(doi)
        result = get_abstract(doi)
        assert result.abstract is None
        assert "not a publication" in result.error
        assert result.attempts == []

    @responses.activate
    def test_datacite_software_refused(self) -> None:
        """DataCite Software DOI — unmapped NMDC category but clearly not a publication."""
        doi = "10.5281/zenodo.1234567"
        _mock_classify_as_datacite_software(doi)
        result = get_abstract(doi)
        assert result.abstract is None
        assert "Software" in result.error
        assert "not a publication type" in result.error
        assert result.attempts == []

    @responses.activate
    def test_crossref_component_refused(self) -> None:
        """Crossref component (figure/table) — not a publication."""
        doi = "10.1038/s41564-020-00861-0.fig1"
        _mock_classify_as_crossref_component(doi)
        result = get_abstract(doi)
        assert result.abstract is None
        assert "component" in result.error
        assert "not a publication type" in result.error
        assert result.attempts == []

    @responses.activate
    def test_skip_classification_bypasses_gate(self) -> None:
        """skip_classification=True goes straight to waterfall."""
        doi = SAMPLE_DOI
        # Only mock OpenAlex (no classification mocks), and have it return nothing
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        responses.add(responses.GET, PUBMED_ID_CONVERTER, json={"records": []})
        responses.add(responses.GET, f"https://doi.org/{doi}", status=404)
        result = get_abstract(doi, skip_classification=True)
        assert "openalex" in result.attempts
        assert result.error == "No abstract found in any source"


class TestGetAbstractWaterfall:
    """Test waterfall ordering — each test mocks classification + sources."""

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
        result = get_abstract(doi)
        assert result.abstract == "Hello world"
        assert result.raw_abstract == '{"Hello": [0], "world": [1]}'
        assert result.source == "openalex"
        assert result.content_format == "inverted_index"
        assert result.attempts == ["openalex"]

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
        result = get_abstract(doi)
        assert result.abstract == "Crossref abstract."
        assert result.raw_abstract == "<jats:p>Crossref abstract.</jats:p>"
        assert result.source == "crossref"
        assert result.content_format == "jats_xml"
        assert result.attempts == ["openalex", "crossref"]

    @responses.activate
    def test_pubmed_fallback(self) -> None:
        """OpenAlex + Crossref miss, PubMed has it."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        # OpenAlex miss
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        # Crossref miss
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        # Elsevier skips non-10.1016 DOIs without an API call
        # Springer Nature skips when SPRINGER_NATURE_API_KEY is absent
        # PubMed ID converter
        responses.add(
            responses.GET,
            PUBMED_ID_CONVERTER,
            json={"records": [{"pmid": "12345678"}]},
        )
        # PubMed efetch
        responses.add(responses.GET, PUBMED_EFETCH, body="PubMed abstract text here.")
        result = get_abstract(doi)
        assert result.abstract == "PubMed abstract text here."
        assert result.raw_abstract == "PubMed abstract text here."
        assert result.source == "pubmed"
        assert result.content_format == "plain_text"
        assert result.pmid == "12345678"
        assert result.attempts == ["openalex", "crossref", "elsevier", "springer_nature", "pubmed"]

    @responses.activate
    def test_content_negotiation_fallback(self) -> None:
        """All others miss, content negotiation provides the abstract."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        # OpenAlex miss
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        # Crossref miss
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        # Elsevier skips non-10.1016 DOIs without an API call
        # Springer Nature skips when SPRINGER_NATURE_API_KEY is absent
        # PubMed miss
        responses.add(responses.GET, PUBMED_ID_CONVERTER, json={"records": []})
        # Content negotiation hit
        responses.add(
            responses.GET,
            f"https://doi.org/{doi}",
            json={"abstract": "Content negotiation abstract."},
        )
        result = get_abstract(doi)
        assert result.abstract == "Content negotiation abstract."
        assert result.raw_abstract == "Content negotiation abstract."
        assert result.source == "content_negotiation"
        assert result.content_format == "citeproc_json"
        assert result.attempts == [
            "openalex", "crossref", "elsevier", "springer_nature", "pubmed", "content_negotiation"
        ]

    @responses.activate
    def test_content_negotiation_note_is_not_used(self) -> None:
        """Abstract retrieval intentionally does not fall back to citeproc note."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        # Elsevier skips non-10.1016 DOIs without an API call
        # Springer Nature skips when SPRINGER_NATURE_API_KEY is absent
        responses.add(responses.GET, PUBMED_ID_CONVERTER, json={"records": []})
        responses.add(
            responses.GET,
            f"https://doi.org/{doi}",
            json={"note": "Context note only, no abstract"},
        )
        result = get_abstract(doi)
        assert result.abstract is None
        assert result.error == "No abstract found in any source"

    @responses.activate
    def test_all_miss(self) -> None:
        """All sources return nothing — graceful None."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        # Elsevier skips non-10.1016 DOIs without an API call
        # Springer Nature skips when SPRINGER_NATURE_API_KEY is absent
        responses.add(responses.GET, PUBMED_ID_CONVERTER, json={"records": []})
        responses.add(responses.GET, f"https://doi.org/{doi}", json={})
        result = get_abstract(doi)
        assert result.abstract is None
        assert result.error == "No abstract found in any source"
        assert len(result.attempts) == len(ALL_SOURCES)

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
            f"{OPENALEX_API_URL}/{doi}",
            body=RequestsConnectionError("down"),
        )
        # Elsevier skips non-10.1016 DOIs without an API call
        # Springer Nature skips when SPRINGER_NATURE_API_KEY is absent
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
        result = get_abstract(doi)
        assert result.abstract is None
        assert len(result.attempts) == len(ALL_SOURCES)

    @responses.activate
    def test_attempts_tracking(self) -> None:
        """The attempts list records what was tried, in order."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(
            responses.GET,
            f"{CROSSREF_API_URL}/{doi}",
            json={"message": {"abstract": "<jats:p>Found it.</jats:p>"}},
        )
        result = get_abstract(doi)
        assert result.attempts == ["openalex", "crossref"]
        assert result.source == "crossref"


class TestGetAbstractResult:
    """Test the SourceRetrievalResult model."""

    def test_defaults(self) -> None:
        r = SourceRetrievalResult(doi="10.1234/test")
        assert r.abstract is None
        assert r.raw_abstract is None
        assert r.source is None
        assert r.content_format is None
        assert r.pmid is None
        assert r.attempts == []
        assert r.error is None


class TestContentFormatClassification:
    """Test that content_format correctly identifies the raw response format."""

    @responses.activate
    def test_crossref_plain_text_format(self) -> None:
        """Crossref abstract without XML tags → plain_text, raw == abstract."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(
            responses.GET,
            f"{CROSSREF_API_URL}/{doi}",
            json={"message": {"abstract": "A plain text abstract with no XML."}},
        )
        result = get_abstract(doi)
        assert result.content_format == "plain_text"
        assert result.abstract == "A plain text abstract with no XML."
        assert result.raw_abstract == result.abstract

    @responses.activate
    def test_crossref_jats_raw_preserved(self) -> None:
        """Crossref JATS → abstract is stripped but raw_abstract has original XML."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        jats = "<jats:p>An <jats:italic>important</jats:italic> finding.</jats:p>"
        responses.add(
            responses.GET,
            f"{CROSSREF_API_URL}/{doi}",
            json={"message": {"abstract": jats}},
        )
        result = get_abstract(doi)
        assert result.abstract == "An important finding."
        assert result.raw_abstract == jats

    @responses.activate
    def test_content_negotiation_jats_raw_preserved(self) -> None:
        """Content negotiation JATS → raw_abstract has original tags."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        # Elsevier skips non-10.1016 DOIs without an API call
        # Springer Nature skips when SPRINGER_NATURE_API_KEY is absent
        responses.add(responses.GET, PUBMED_ID_CONVERTER, json={"records": []})
        jats = "<jats:p>Tagged abstract.</jats:p>"
        responses.add(
            responses.GET,
            f"https://doi.org/{doi}",
            json={"abstract": jats},
        )
        result = get_abstract(doi)
        assert result.content_format == "jats_xml"
        assert result.abstract == "Tagged abstract."
        assert result.raw_abstract == jats

    @responses.activate
    def test_openalex_raw_is_json(self) -> None:
        """OpenAlex raw_abstract is the JSON-serialized inverted index."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        inverted = {"Hello": [0], "world": [1]}
        responses.add(
            responses.GET,
            f"{OPENALEX_API_URL}/https://doi.org/{doi}",
            json={"abstract_inverted_index": inverted},
        )
        result = get_abstract(doi)
        assert result.abstract == "Hello world"
        import json

        assert json.loads(result.raw_abstract) == inverted


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
        result = get_abstract(doi, sources=["crossref"])
        assert result.abstract == "Crossref only."
        assert result.source == "crossref"
        assert result.attempts == ["crossref"]

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
        result = get_abstract(doi, sources=["pubmed"])
        assert result.abstract == "PubMed only text."
        assert result.source == "pubmed"
        assert result.pmid == "99999"
        assert result.attempts == ["pubmed"]

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
        result = get_abstract(doi, sources=["crossref", "openalex"])
        assert result.source == "crossref"
        assert result.attempts == ["crossref"]

    @responses.activate
    def test_subset_miss_all(self) -> None:
        """Two sources tried, both miss — error and correct attempts list."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        result = get_abstract(doi, sources=["openalex", "crossref"])
        assert result.abstract is None
        assert result.error == "No abstract found in any source"
        assert result.attempts == ["openalex", "crossref"]

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
        result = get_abstract(doi, sources=["bogus", "crossref"])
        assert result.abstract == "Found it."
        assert result.attempts == ["crossref"]

    def test_all_sources_constant(self) -> None:
        """ALL_SOURCES has the expected default order."""
        assert ALL_SOURCES == (
            "openalex", "crossref", "elsevier", "springer_nature",
            "pubmed", "content_negotiation", "osti",
        )

    @responses.activate
    def test_default_sources_same_as_before(self) -> None:
        """No sources= arg gives the same behavior as the original waterfall."""
        doi = SAMPLE_DOI
        _mock_classify_as_publication(doi)
        responses.add(responses.GET, f"{OPENALEX_API_URL}/https://doi.org/{doi}", json={})
        responses.add(responses.GET, f"{CROSSREF_API_URL}/{doi}", json={"message": {}})
        # Elsevier skips non-10.1016 DOIs without an API call
        # Springer Nature skips when SPRINGER_NATURE_API_KEY is absent
        responses.add(responses.GET, PUBMED_ID_CONVERTER, json={"records": []})
        responses.add(responses.GET, f"https://doi.org/{doi}", json={})
        result = get_abstract(doi)
        assert result.attempts == [
            "openalex", "crossref", "elsevier", "springer_nature",
            "pubmed", "content_negotiation", "osti",
        ]


# ---------------------------------------------------------------------------
# Integration tests — hit real APIs, skip in CI
# ---------------------------------------------------------------------------


# -- Issue #1597 target prefixes --
# Each test covers one of the 6 assigned prefixes from microbiomedata/issues#1597.
# DOIs are taken from the curated fixture (tests/fixtures/doi_test_cases.json).


@integration
def test_get_abstract_real_aslo_doi() -> None:
    """10.4319 — ASLO (limnology/oceanography) journal article."""
    result = get_abstract("10.4319/lom.2008.6.230")
    assert (
        result.abstract is not None
    ), f"No abstract found: {result.error} (tried {result.attempts})"
    assert len(result.abstract) > 50
    assert result.source is not None
    assert result.raw_abstract is not None
    assert result.content_format is not None


@integration
def test_get_abstract_real_nature_doi() -> None:
    """10.1038 — Nature journal article."""
    result = get_abstract("10.1038/s41564-020-00861-0")
    assert (
        result.abstract is not None
    ), f"No abstract found: {result.error} (tried {result.attempts})"
    assert len(result.abstract) > 100
    assert result.raw_abstract is not None
    assert result.content_format is not None


@integration
def test_get_abstract_real_frontiers_doi() -> None:
    """10.3389 — Frontiers journal article."""
    result = get_abstract("10.3389/fsoil.2023.1120425")
    assert (
        result.abstract is not None
    ), f"No abstract found: {result.error} (tried {result.attempts})"
    assert len(result.abstract) > 100
    assert result.source in ("openalex", "crossref")
    assert result.raw_abstract is not None


@integration
def test_get_abstract_real_elsevier_doi() -> None:
    """10.1016 — Elsevier journal article.

    With the Elsevier ScienceDirect API wired into the waterfall (step 2.5),
    this should now succeed when ELSEVIER_API_KEY is set. Without the key,
    Elsevier is skipped and the other sources are tried.
    """
    result = get_abstract("10.1016/j.apsoil.2025.106110")
    if result.abstract:
        assert result.raw_abstract is not None
        assert result.content_format is not None
    else:
        assert result.error == "No abstract found in any source"
        assert len(result.attempts) == len(ALL_SOURCES)


@integration
def test_get_abstract_real_soil_science_doi() -> None:
    """10.2136 — Soil Science Society of America book chapter."""
    result = get_abstract("10.2136/sssabookser5.3.c16")
    # Book chapters may or may not have abstracts; verify graceful handling
    assert result.error is None or "No abstract found" in result.error
    if result.abstract:
        assert result.raw_abstract is not None
        assert result.content_format is not None


@integration
def test_get_abstract_real_protocols_io_doi() -> None:
    """10.17504 — protocols.io (DataCite). Not a standard publication, NMDC category unmapped."""
    result = get_abstract("10.17504/protocols.io.kxygxyydkl8j/v1")
    # Category is unmapped (None), so gate allows it through.
    # protocols.io DOIs typically don't have abstracts in the traditional sources.
    assert result.error is None or "No abstract found" in result.error
    # Should NOT be refused as non-publication (category is None, not dataset/award)
    if result.error:
        assert "not a publication" not in result.error


# -- Other integration tests --


@integration
def test_get_abstract_real_dataset_doi() -> None:
    """Dataset DOI — should be gracefully refused (not a publication)."""
    result = get_abstract("10.15485/1729719")
    assert result.abstract is None
    assert result.error is not None
    assert "not a publication" in result.error


@integration
def test_get_abstract_raw_vs_cleaned() -> None:
    """Verify raw_abstract differs from abstract when JATS XML is present."""
    result = get_abstract("10.3389/fsoil.2023.1120425")
    assert result.abstract is not None
    assert result.raw_abstract is not None
    if result.content_format == "jats_xml":
        assert "<" in result.raw_abstract
        assert "<" not in result.abstract
    elif result.content_format == "inverted_index":
        assert "{" in result.raw_abstract
        assert result.abstract != result.raw_abstract


# ---------------------------------------------------------------------------
# Elsevier ScienceDirect — unit tests (mocked HTTP)
# ---------------------------------------------------------------------------

ELSEVIER_DOI = "10.1016/j.apsoil.2025.106110"

ELSEVIER_API_RESPONSE = {
    "full-text-retrieval-response": {
        "coredata": {
            "dc:title": "Soil microbial community response to long-term fertilization",
            "dc:description": (
                "Abstract   Soil microbial communities play a crucial role "
                "in nutrient cycling. This study examined the effects of "
                "long-term fertilization on soil microbial diversity."
            ),
            "prism:doi": ELSEVIER_DOI,
            "prism:publicationName": "Applied Soil Ecology",
        }
    }
}


class TestElsevierUnit:
    """Unit tests for Elsevier ScienceDirect abstract retrieval."""

    @responses.activate
    def test_elsevier_returns_abstract(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: API key set, 10.1016 DOI, abstract returned."""
        monkeypatch.setenv("ELSEVIER_API_KEY", "fake-api-key")
        responses.add(
            responses.GET,
            f"{ELSEVIER_API_URL}/{ELSEVIER_DOI}",
            json=ELSEVIER_API_RESPONSE,
        )
        result = get_abstract(ELSEVIER_DOI, skip_classification=True, sources=["elsevier"])
        assert result.abstract is not None
        assert "nutrient cycling" in result.abstract
        assert result.source == "elsevier"
        assert result.content_format == "plain_text"
        assert result.raw_abstract is not None
        assert result.attempts == ["elsevier"]

    @responses.activate
    def test_elsevier_skips_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Graceful skip when ELSEVIER_API_KEY is not set."""
        monkeypatch.delenv("ELSEVIER_API_KEY", raising=False)
        result = get_abstract(ELSEVIER_DOI, skip_classification=True, sources=["elsevier"])
        assert result.abstract is None
        assert result.error == "No abstract found in any source"

    @responses.activate
    def test_elsevier_skips_non_1016_doi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Prefix gate: non-10.1016 DOIs skip Elsevier without an API call."""
        monkeypatch.setenv("ELSEVIER_API_KEY", "fake-api-key")
        result = get_abstract(SAMPLE_DOI, skip_classification=True, sources=["elsevier"])
        assert result.abstract is None
        # No HTTP call should have been made
        assert len(responses.calls) == 0

    @responses.activate
    def test_elsevier_handles_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP error (403/500) is handled gracefully."""
        monkeypatch.setenv("ELSEVIER_API_KEY", "fake-api-key")
        responses.add(
            responses.GET,
            f"{ELSEVIER_API_URL}/{ELSEVIER_DOI}",
            status=403,
        )
        result = get_abstract(ELSEVIER_DOI, skip_classification=True, sources=["elsevier"])
        assert result.abstract is None
        assert result.error == "No abstract found in any source"

    @responses.activate
    def test_elsevier_handles_no_abstract(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Response missing dc:description is handled gracefully."""
        monkeypatch.setenv("ELSEVIER_API_KEY", "fake-api-key")
        no_abstract_response = {
            "full-text-retrieval-response": {
                "coredata": {
                    "dc:title": "Article without abstract",
                    "prism:doi": ELSEVIER_DOI,
                }
            }
        }
        responses.add(
            responses.GET,
            f"{ELSEVIER_API_URL}/{ELSEVIER_DOI}",
            json=no_abstract_response,
        )
        result = get_abstract(ELSEVIER_DOI, skip_classification=True, sources=["elsevier"])
        assert result.abstract is None

    @responses.activate
    def test_elsevier_in_waterfall_after_crossref(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Elsevier sits between Crossref and PubMed in the waterfall."""
        monkeypatch.setenv("ELSEVIER_API_KEY", "fake-api-key")
        doi = ELSEVIER_DOI
        # OpenAlex miss
        responses.add(
            responses.GET,
            f"{OPENALEX_API_URL}/https://doi.org/{doi}",
            json={},
        )
        # Crossref miss
        responses.add(
            responses.GET,
            f"{CROSSREF_API_URL}/{doi}",
            json={"message": {}},
        )
        # Elsevier hit
        responses.add(
            responses.GET,
            f"{ELSEVIER_API_URL}/{doi}",
            json=ELSEVIER_API_RESPONSE,
        )
        result = get_abstract(doi, skip_classification=True)
        assert result.source == "elsevier"
        assert result.attempts == ["openalex", "crossref", "elsevier"]
        assert "nutrient cycling" in result.abstract


# ---------------------------------------------------------------------------
# Elsevier ScienceDirect — integration tests (real API, gated on API key)
# ---------------------------------------------------------------------------


@integration
def test_elsevier_live_returns_abstract() -> None:
    """Real API call for a recent Elsevier DOI (requires ELSEVIER_API_KEY)."""
    import os

    if not os.environ.get("ELSEVIER_API_KEY"):
        pytest.skip("ELSEVIER_API_KEY not set")
    result = get_abstract("10.1016/j.apsoil.2025.106110", sources=["elsevier"])
    assert result.abstract is not None, f"No abstract: {result.error}"
    assert len(result.abstract) > 50
    assert result.source == "elsevier"
    assert result.content_format == "plain_text"


@integration
def test_elsevier_live_old_format_doi() -> None:
    """Edge case: 1985 DOI with parentheses (requires ELSEVIER_API_KEY)."""
    import os

    if not os.environ.get("ELSEVIER_API_KEY"):
        pytest.skip("ELSEVIER_API_KEY not set")
    result = get_abstract(
        "10.1016/0038-0717(85)90144-0", sources=["elsevier"]
    )
    # Old DOIs may or may not have abstracts in the API
    if result.abstract:
        assert result.source == "elsevier"
        assert len(result.abstract) > 20


# ---------------------------------------------------------------------------
# Springer Nature Metadata API — unit tests
# ---------------------------------------------------------------------------

SPRINGER_NATURE_DOI = "10.1038/s41564-020-00861-0"

SPRINGER_NATURE_API_RESPONSE = {
    "apiMessage": "This JSON was provided by Springer Nature",
    "query": f"doi:{SPRINGER_NATURE_DOI}",
    "result": {
        "total": "1",
        "start": "1",
        "pageLength": "1",
        "recordsDisplayed": "1",
    },
    "records": [
        {
            "title": "A genomic catalog of Earth\u2019s microbiomes",
            "doi": SPRINGER_NATURE_DOI,
            "abstract": (
                "The reconstruction of bacterial and archaeal genomes from "
                "metagenomes has enabled insights into the ecology and evolution "
                "of environmental and host-associated microbiomes."
            ),
            "publicationName": "Nature Microbiology",
            "publicationDate": "2021-01-01",
            "contentType": "Article",
        }
    ],
}


class TestSpringerNatureUnit:
    """Unit tests for Springer Nature Metadata API abstract retrieval."""

    @responses.activate
    def test_springer_nature_returns_abstract(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: API key set, 10.1038 DOI, abstract returned."""
        monkeypatch.setenv("SPRINGER_NATURE_API_KEY", "fake-api-key")
        responses.add(
            responses.GET,
            SPRINGER_NATURE_META_API_URL,
            json=SPRINGER_NATURE_API_RESPONSE,
        )
        result = get_abstract(
            SPRINGER_NATURE_DOI, skip_classification=True, sources=["springer_nature"],
        )
        assert result.abstract is not None
        assert "metagenomes" in result.abstract
        assert result.source == "springer_nature"
        assert result.content_format == "plain_text"
        assert result.raw_abstract is not None
        assert result.attempts == ["springer_nature"]

    @responses.activate
    def test_springer_nature_skips_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Graceful skip when SPRINGER_NATURE_API_KEY is not set."""
        monkeypatch.delenv("SPRINGER_NATURE_API_KEY", raising=False)
        result = get_abstract(
            SPRINGER_NATURE_DOI, skip_classification=True, sources=["springer_nature"],
        )
        assert result.abstract is None
        assert result.error == "No abstract found in any source"

    @responses.activate
    def test_springer_nature_skips_non_matching_prefix(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prefix gate: non-10.1038/10.1007 DOIs skip without an API call."""
        monkeypatch.setenv("SPRINGER_NATURE_API_KEY", "fake-api-key")
        result = get_abstract(
            "10.1016/j.example.2025.123",
            skip_classification=True,
            sources=["springer_nature"],
        )
        assert result.abstract is None
        assert len(responses.calls) == 0

    @responses.activate
    def test_springer_nature_accepts_10_1007_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """10.1007/ prefix (Springer) is also accepted."""
        monkeypatch.setenv("SPRINGER_NATURE_API_KEY", "fake-api-key")
        springer_doi = "10.1007/s00248-021-01234-5"
        springer_response = {
            "records": [
                {
                    "title": "Springer Article",
                    "doi": springer_doi,
                    "abstract": "A study of microbial ecology in forest soils.",
                }
            ],
        }
        responses.add(
            responses.GET,
            SPRINGER_NATURE_META_API_URL,
            json=springer_response,
        )
        result = get_abstract(springer_doi, skip_classification=True, sources=["springer_nature"])
        assert result.abstract is not None
        assert "microbial ecology" in result.abstract
        assert result.source == "springer_nature"

    @responses.activate
    def test_springer_nature_handles_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP error (401/500) is handled gracefully."""
        monkeypatch.setenv("SPRINGER_NATURE_API_KEY", "fake-api-key")
        responses.add(
            responses.GET,
            SPRINGER_NATURE_META_API_URL,
            status=401,
        )
        result = get_abstract(
            SPRINGER_NATURE_DOI, skip_classification=True, sources=["springer_nature"],
        )
        assert result.abstract is None
        assert result.error == "No abstract found in any source"

    @responses.activate
    def test_springer_nature_handles_no_abstract(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Response record missing abstract is handled gracefully."""
        monkeypatch.setenv("SPRINGER_NATURE_API_KEY", "fake-api-key")
        no_abstract_response = {
            "records": [
                {
                    "title": "Article without abstract",
                    "doi": SPRINGER_NATURE_DOI,
                }
            ],
        }
        responses.add(
            responses.GET,
            SPRINGER_NATURE_META_API_URL,
            json=no_abstract_response,
        )
        result = get_abstract(
            SPRINGER_NATURE_DOI, skip_classification=True, sources=["springer_nature"],
        )
        assert result.abstract is None

    @responses.activate
    def test_springer_nature_handles_empty_records(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Response with empty records array is handled gracefully."""
        monkeypatch.setenv("SPRINGER_NATURE_API_KEY", "fake-api-key")
        empty_response = {"records": []}
        responses.add(
            responses.GET,
            SPRINGER_NATURE_META_API_URL,
            json=empty_response,
        )
        result = get_abstract(
            SPRINGER_NATURE_DOI, skip_classification=True, sources=["springer_nature"],
        )
        assert result.abstract is None

    @responses.activate
    def test_springer_nature_in_waterfall_after_elsevier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Springer Nature sits between Elsevier and PubMed in the waterfall."""
        monkeypatch.setenv("SPRINGER_NATURE_API_KEY", "fake-api-key")
        doi = SPRINGER_NATURE_DOI
        # OpenAlex miss
        responses.add(
            responses.GET,
            f"{OPENALEX_API_URL}/https://doi.org/{doi}",
            json={},
        )
        # Crossref miss
        responses.add(
            responses.GET,
            f"{CROSSREF_API_URL}/{doi}",
            json={"message": {}},
        )
        # Elsevier skips non-10.1016 DOIs without an API call
        # Springer Nature hit
        responses.add(
            responses.GET,
            SPRINGER_NATURE_META_API_URL,
            json=SPRINGER_NATURE_API_RESPONSE,
        )
        result = get_abstract(doi, skip_classification=True)
        assert result.source == "springer_nature"
        assert result.attempts == ["openalex", "crossref", "elsevier", "springer_nature"]
        assert "metagenomes" in result.abstract


# ---------------------------------------------------------------------------
# Springer Nature Metadata API — integration tests (real API, gated on API key)
# ---------------------------------------------------------------------------


@integration
def test_springer_nature_live_returns_abstract() -> None:
    """Real API call for a Nature DOI (requires SPRINGER_NATURE_API_KEY)."""
    import os

    if not os.environ.get("SPRINGER_NATURE_API_KEY"):
        pytest.skip("SPRINGER_NATURE_API_KEY not set")
    result = get_abstract("10.1038/s41564-020-00861-0", sources=["springer_nature"])
    assert result.abstract is not None, f"No abstract: {result.error}"
    assert len(result.abstract) > 50
    assert result.source == "springer_nature"
    assert result.content_format == "plain_text"


@integration
def test_springer_nature_live_springer_prefix() -> None:
    """Real API call for a Springer DOI (10.1007 prefix, requires SPRINGER_NATURE_API_KEY)."""
    import os

    if not os.environ.get("SPRINGER_NATURE_API_KEY"):
        pytest.skip("SPRINGER_NATURE_API_KEY not set")
    result = get_abstract("10.1007/s00248-021-01700-x", sources=["springer_nature"])
    # Springer DOIs may or may not have abstracts in the Metadata API
    if result.abstract:
        assert result.source == "springer_nature"
        assert len(result.abstract) > 20
