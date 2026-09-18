"""Tests for OSTI DOI retrieval via the single `try_osti` entry point."""

import pytest
import responses

from nmdc_metadata_suggestor_ai_tool.constants import OSTI_API_URL, OSTI_E2_API_URL
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.osti import try_osti
from nmdc_metadata_suggestor_ai_tool.models.resolver_context import ResolverContext

integration = pytest.mark.integration

# Test DOIs from OSTI
OSTI_DOIS = ["10.15485/2478895", "10.15485/1729719", "10.15485/1603775"]


# ---------------------------------------------------------------------------
# Unit tests with mocked HTTP
# ---------------------------------------------------------------------------


@responses.activate
def test_try_osti_retrieve_abstract_success() -> None:
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
    result = try_osti(doi, errors=errors)

    assert isinstance(result, ResolverContext)
    assert result.text == abstract_text
    assert result.raw_text == abstract_text
    assert result.source == "osti"
    assert result.urls is None
    assert result.publication_dois is None
    assert errors == []


@responses.activate
def test_try_osti_no_description_or_pdf_returns_none() -> None:
    """Return None when OSTI record has neither description nor fulltext PDF link."""
    doi = "10.15485/1234567"

    responses.add(
        responses.GET,
        OSTI_E2_API_URL,
        json=[{"doi": doi, "product_type": "Dataset"}],
        status=200,
    )

    errors: list[str] = []
    result = try_osti(doi, errors=errors)

    assert result is None
    assert any("No abstract/description found" in error for error in errors)
    assert any("no abstract/description or pdf link" in error.lower() for error in errors)


@responses.activate
def test_try_osti_uses_osti_pdf_link_for_journal_article() -> None:
    """Include OSTI fulltext link from journal article records in the context URLs."""
    requested_doi = "10.15485/1234567"
    journal_article_doi = "10.1038/s41564-020-00861-0"
    abstract_text = "Test abstract."
    pdf_url = "https://example.com/article.pdf"

    responses.add(
        responses.GET,
        OSTI_E2_API_URL,
        json=[
            {
                "product_type": "Journal Article",
                "doi": journal_article_doi,
                "description": abstract_text,
                "links": [
                    {"rel": "self", "href": "https://example.com/record"},
                    {"rel": "fulltext", "href": pdf_url},
                ],
            }
        ],
        status=200,
    )

    result = try_osti(requested_doi)

    assert isinstance(result, ResolverContext)
    assert result.text == abstract_text
    assert result.raw_text == abstract_text
    assert result.urls == [pdf_url]
    assert result.publication_dois[0] == journal_article_doi
    assert result.source == "osti"


@responses.activate
def test_try_osti_pdf_link_without_description_returns_context_with_empty_text() -> None:
    """Return context when a fulltext link exists even if description is missing."""
    requested_doi = "10.15485/1234567"
    journal_article_doi = "10.1038/s41564-020-00861-0"
    pdf_url = "https://example.com/article.pdf"

    responses.add(
        responses.GET,
        OSTI_E2_API_URL,
        json=[
            {
                "product_type": "Journal Article",
                "doi": journal_article_doi,
                "links": [{"rel": "fulltext", "href": pdf_url}],
            }
        ],
        status=200,
    )

    errors: list[str] = []
    result = try_osti(requested_doi, errors=errors)

    assert isinstance(result, ResolverContext)
    assert result.text is None
    assert result.raw_text is None
    assert result.urls == [pdf_url]
    assert result.publication_dois[0] == journal_article_doi
    assert any("No abstract/description found" in error for error in errors)
    assert not any("no abstract/description or pdf link" in error.lower() for error in errors)


@responses.activate
def test_try_osti_404_returns_none_with_errors() -> None:
    """Handle 404 responses from both E2 and fallback OSTI API endpoints."""
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
    result = try_osti(doi=doi, errors=errors)

    assert result is None
    assert len(errors) > 0


# ---------------------------------------------------------------------------
# Integration tests with real APIs
# ---------------------------------------------------------------------------


@integration
def test_try_osti_abstract_retrieval_real_dois() -> None:
    """Try abstract retrieval with representative real OSTI DOIs."""
    results: dict[str, ResolverContext | None] = {}

    for doi in OSTI_DOIS:
        errors: list[str] = []
        result = try_osti(doi, errors=errors)
        results[doi] = result
        assert result is None or isinstance(result, ResolverContext)
        assert (result and result.text) or errors

    successful = [resolved for resolved in results.values() if resolved and resolved.text]
    assert len(successful) >= 1


@integration
def test_try_osti_waterfall_to_v1_for_known_doi() -> None:
    """Exercise E2 -> v1 fallback for a DOI known to be inconsistently indexed in E2."""
    doi = "10.15485/1234567"
    errors: list[str] = []
    result = try_osti(doi, errors=errors)

    assert result is None or isinstance(result, ResolverContext)
    assert (result and result.text) or errors


@integration
def test_try_osti_invalid_osti_doi() -> None:
    """Return errors for invalid OSTI DOI."""
    doi = "10.15485/9999999999"
    errors: list[str] = []
    result = try_osti(doi, errors=errors)

    assert result is None
    assert len(errors) > 0
