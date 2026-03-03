"""Tests for OSTI publication retrieval."""

import pytest
import responses

from nmdc_metadata_suggestor.constants import (
    CROSSREF_API_URL,
    EUROPEPMC_API_URL,
    OSTI_API_URL,
    OSTI_E2_API_URL,
)
from nmdc_metadata_suggestor.doi_ingestion.osti import (
    retrieve_pdf_link_from_osti_doi,
)
from nmdc_metadata_suggestor.doi_ingestion.osti import (
    try_osti as retrieve_doi_info_from_osti,
)
from nmdc_metadata_suggestor.models.publication import Publication
from nmdc_metadata_suggestor.models.resolver_context import ResolverContext

integration = pytest.mark.integration

# Test DOIs from OSTI
OSTI_DOIS = ["10.15485/2478895", "10.15485/1729719", "10.15485/1603775"]


# ---------------------------------------------------------------------------
# Unit tests with mocked HTTP
# ---------------------------------------------------------------------------


@responses.activate
def test_retrieve_abstract_success():
    """Retrieve abstract successfully from OSTI."""
    doi = "10.15485/1729719"
    abstract_text = "This is a test abstract."

    responses.add(
        responses.GET,
        OSTI_E2_API_URL,
        json=[{"doi": doi, "description": abstract_text}],
        status=200,
    )

    errors: list[str] = []
    result = retrieve_doi_info_from_osti(doi, errors=errors)

    assert isinstance(result, ResolverContext)
    assert result.text == abstract_text
    assert result.source == "osti"
    assert errors == []


@responses.activate
def test_retrieve_abstract_no_description():
    """Handle OSTI record with no description."""
    doi = "10.15485/1234567"

    responses.add(
        responses.GET,
        OSTI_E2_API_URL,
        json=[{"doi": doi}],
        status=200,
    )

    errors: list[str] = []
    result = retrieve_doi_info_from_osti(doi, errors=errors)

    assert result is None
    assert any("No abstract/description found" in error for error in errors)


@responses.activate
def test_retrieve_abstract_404():
    """Handle 404 error from OSTI API."""
    doi = "10.15485/9999999"

    responses.add(
        responses.GET,
        OSTI_E2_API_URL,
        status=404,
    )
    responses.add(
        responses.GET,
        OSTI_API_URL,
        status=404,
    )

    errors: list[str] = []
    result = retrieve_doi_info_from_osti(doi=doi, errors=errors)

    assert result is None
    assert len(errors) > 0


@responses.activate
def test_retrieve_publication_journal_article():
    """Retrieve publication with PDF links from journal article."""
    osti_doi = "10.15485/1234567"
    ja_doi = "10.1038/s41564-020-00861-0"
    abstract_text = "Test abstract."
    pdf_url = "https://example.com/article.pdf"

    responses.add(
        responses.GET,
        OSTI_E2_API_URL,
        json=[
            {
                "product_type": "Journal Article",
                "doi": ja_doi,
                "description": abstract_text,
            }
        ],
        status=200,
    )

    responses.add(
        responses.GET,
        f"{CROSSREF_API_URL}/{ja_doi}",
        json={"message": {"link": [{"URL": pdf_url, "content-type": "application/pdf"}]}},
        status=200,
    )

    result = retrieve_pdf_link_from_osti_doi(osti_doi)

    assert isinstance(result, Publication)
    assert result.doi == osti_doi
    assert result.associated_publication_doi == ja_doi
    assert result.abstract == abstract_text
    assert pdf_url in str(result.urls)


@responses.activate
def test_retrieve_publication_dataset():
    """Handle dataset product type."""
    doi = "10.15485/1729719"

    responses.add(
        responses.GET,
        OSTI_E2_API_URL,
        json=[
            {
                "product_type": "Dataset",
                "doi": doi,
                "description": "Dataset description.",
            }
        ],
        status=200,
    )

    result = retrieve_pdf_link_from_osti_doi(doi)

    assert result.doi == doi
    assert result.associated_publication_doi is None
    assert result.urls is None


@responses.activate
def test_retrieve_publication_pmc_fallback():
    """Fallback to PMC when Crossref has no PDFs."""
    osti_doi = "10.15485/1234567"
    ja_doi = "10.1038/s41564-020-00861-0"
    pmcid = "PMC1234567"

    responses.add(
        responses.GET,
        OSTI_E2_API_URL,
        json=[
            {
                "product_type": "Journal Article",
                "doi": ja_doi,
                "description": "Abstract.",
            }
        ],
        status=200,
    )

    responses.add(
        responses.GET,
        f"{CROSSREF_API_URL}/{ja_doi}",
        json={"message": {"link": []}},
        status=200,
    )

    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json={"resultList": {"result": [{"pmcid": pmcid, "isOpenAccess": "Y"}]}},
        status=200,
    )

    result = retrieve_pdf_link_from_osti_doi(osti_doi)

    assert result.source == "PMC"
    assert f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/" in str(result.urls)


@responses.activate
def test_retrieve_publication_404():
    """Handle 404 error for publication retrieval."""
    doi = "10.15485/9999999"

    responses.add(
        responses.GET,
        OSTI_E2_API_URL,
        status=404,
    )
    responses.add(
        responses.GET,
        OSTI_API_URL,
        status=404,
    )

    result = retrieve_pdf_link_from_osti_doi(doi)

    assert isinstance(result, Publication)
    assert result.doi == doi
    assert result.error is not None
    assert result.abstract is None


# ---------------------------------------------------------------------------
# Integration tests with real APIs
# ---------------------------------------------------------------------------


@integration
def test_abstract_retrieval_real_dois():
    """Test abstract retrieval with all three real OSTI DOIs."""
    results = {}

    for doi in OSTI_DOIS:
        errors: list[str] = []
        result = retrieve_doi_info_from_osti(doi, errors=errors)
        results[doi] = result
        assert result is None or isinstance(result, ResolverContext)
        assert (result and result.text) or errors

    # At least one should have content
    successful = [r for r in results.values() if r and r.text]
    assert len(successful) >= 1


@integration
def test_waterfall():
    """Test full waterfall retrieval with all three real OSTI DOIs."""
    # doi that is in the v1 but not in E2
    doi = "10.15485/1234567"
    errors: list[str] = []
    result = retrieve_doi_info_from_osti(doi, errors=errors)
    assert result is None or isinstance(result, ResolverContext)
    assert (result and result.text) or errors


@integration
def test_publication_retrieval_real_dois():
    """Test full publication retrieval with all three real OSTI DOIs."""
    for doi in OSTI_DOIS:
        result = retrieve_pdf_link_from_osti_doi(doi)
        assert isinstance(result, Publication)
        assert result.doi == doi
        # Should have abstract or error (but not raise exception)
        assert result.abstract or result.error


@integration
def test_invalid_osti_doi():
    """Test handling of invalid OSTI DOI."""
    doi = "10.15485/9999999999"
    errors: list[str] = []
    result = retrieve_doi_info_from_osti(doi, errors=errors)

    assert result is None
    assert len(errors) > 0


@integration
def test_doi_10_15485_2478895():
    """Test DOI 10.15485/2478895 specifically."""
    errors: list[str] = []
    result = retrieve_doi_info_from_osti("10.15485/2478895", errors=errors)
    assert (result and result.text) or errors


@integration
def test_doi_10_15485_1729719():
    """Test DOI 10.15485/1729719 specifically."""
    errors: list[str] = []
    result = retrieve_doi_info_from_osti("10.15485/1729719", errors=errors)
    assert (result and result.text) or errors


@integration
def test_doi_10_15485_1603775():
    """Test DOI 10.15485/1603775 specifically."""
    errors: list[str] = []
    result = retrieve_doi_info_from_osti("10.15485/1603775", errors=errors)
    assert (result and result.text) or errors
