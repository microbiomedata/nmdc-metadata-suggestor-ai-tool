"""Tests for OSTI publication retrieval."""

import pytest
import responses

from nmdc_metadata_suggestor.constants import (
    CROSSREF_API_URL,
    EUROPEPMC_API_URL,
    OSTI_API_URL,
    OSTI_E2_API_URL,
)
from nmdc_metadata_suggestor.models.doi import AbstractResult
from nmdc_metadata_suggestor.models.publication import Publication
from nmdc_metadata_suggestor.publication_ingestion.osti_publication_retriever import (
    retrieve_doi_info_from_osti,
    retrieve_pdf_link_from_osti_doi,
)

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

    result = retrieve_doi_info_from_osti(doi)

    assert isinstance(result, AbstractResult)
    assert result.abstract == abstract_text
    assert result.source == "osti"
    assert result.error is None


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

    result = retrieve_doi_info_from_osti(doi)

    assert result.abstract is None
    assert result.error is not None and "No abstract/description found" in result.error


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

    result = retrieve_doi_info_from_osti(doi=doi)

    assert result.abstract is None
    assert result.error is not None


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
        result = retrieve_doi_info_from_osti(doi)
        results[doi] = result
        assert isinstance(result, AbstractResult)
        assert result.doi == doi
        # Either success with abstract or clean error
        assert result.abstract or result.error

    # At least one should have an abstract
    successful = [r for r in results.values() if r.abstract]
    assert len(successful) >= 1


@integration
def test_waterfall():
    """Test full waterfall retrieval with all three real OSTI DOIs."""
    # doi that is in the v1 but not in E2
    doi = "10.15485/1234567"
    result = retrieve_doi_info_from_osti(doi)
    assert isinstance(result, AbstractResult)
    assert result.doi == doi
    # Should have abstract or error (but not raise exception)
    assert result.abstract or result.error


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
    result = retrieve_doi_info_from_osti(doi)

    assert result.abstract is None
    assert result.error is not None


@integration
def test_doi_10_15485_2478895():
    """Test DOI 10.15485/2478895 specifically."""
    result = retrieve_doi_info_from_osti("10.15485/2478895")
    assert isinstance(result, AbstractResult)
    assert result.abstract or result.error


@integration
def test_doi_10_15485_1729719():
    """Test DOI 10.15485/1729719 specifically."""
    result = retrieve_doi_info_from_osti("10.15485/1729719")
    assert isinstance(result, AbstractResult)
    assert result.abstract or result.error


@integration
def test_doi_10_15485_1603775():
    """Test DOI 10.15485/1603775 specifically."""
    result = retrieve_doi_info_from_osti("10.15485/1603775")
    assert isinstance(result, AbstractResult)
    assert result.abstract or result.error
