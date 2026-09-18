"""Tests for OSTI award DOI description retrieval."""

import responses

from nmdc_metadata_suggestor_ai_tool.constants import OSTI_AWARD_API_URL
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.osti import try_osti_award
from nmdc_metadata_suggestor_ai_tool.models.resolver_context import ResolverContext


@responses.activate
def test_try_osti_award_retrieves_description() -> None:
    """Retrieve and clean an award description from the OSTI award API."""
    award_doi = "10.46936/expl.proj.2024.61472/60012919"
    award_description = "  This is an award description.  "

    responses.add(
        responses.GET,
        OSTI_AWARD_API_URL,
        json={
            "response": {
                "numFound": 1,
                "docs": [
                    {
                        "award_doi": award_doi,
                        "award_description": award_description,
                    }
                ],
            }
        },
        status=200,
    )

    errors: list[str] = []
    result = try_osti_award(award_doi, errors=errors)

    assert isinstance(result, ResolverContext)
    assert result.text == "This is an award description."
    assert result.raw_text == award_description
    assert result.kind == "description"
    assert result.source == "osti_award"
    assert errors == []


@responses.activate
def test_try_osti_award_missing_description_returns_none() -> None:
    """Return None and an error when the award has no description."""
    award_doi = "10.46936/expl.proj.2024.61472/60012919"

    responses.add(
        responses.GET,
        OSTI_AWARD_API_URL,
        json={"response": {"numFound": 1, "docs": [{"award_doi": award_doi}]}},
        status=200,
    )

    errors: list[str] = []
    result = try_osti_award(award_doi, errors=errors)

    assert result is None
    assert any("No description found" in error for error in errors)
    assert any("contained no description" in error for error in errors)


@responses.activate
def test_try_osti_award_no_matching_docs_returns_none() -> None:
    """Return None and an error when the award DOI has no matching records."""
    award_doi = "10.46936/expl.proj.2024.61472/60012919"

    responses.add(
        responses.GET,
        OSTI_AWARD_API_URL,
        json={"response": {"numFound": 0, "docs": []}},
        status=200,
    )

    errors: list[str] = []
    result = try_osti_award(award_doi, errors=errors)

    assert result is None
    assert errors == [f"No award found for award DOI: {award_doi}"]


@responses.activate
def test_try_osti_award_http_error_returns_none() -> None:
    """Return None and an error when the OSTI award API request fails."""
    award_doi = "10.46936/expl.proj.2024.61472/60012919"

    responses.add(responses.GET, OSTI_AWARD_API_URL, status=404)

    errors: list[str] = []
    result = try_osti_award(award_doi, errors=errors)

    assert result is None
    assert errors == ["OSTI Award API request failed: HTTPError"]
