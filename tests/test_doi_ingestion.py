"""Tests for DOI description/abstract ingestion waterfall."""

import responses

from nmdc_metadata_suggestor.doi_ingestion.main import (
    CROSSREF_API,
    DATACITE_API,
    DOI_CONTENT_NEGOTIATION_API,
    EDI_DOI_API,
    ESS_DIVE_API,
    ZENODO_API,
    get_doi_description_or_abstract,
)


def test_invalid_doi_rejected_without_attempts() -> None:
    """Reject malformed DOI input before attempting any source."""
    result = get_doi_description_or_abstract("not-a-doi")
    assert result.context is None
    assert result.attempts == []
    assert result.error is not None
    assert "Invalid DOI" in result.error


@responses.activate
def test_datacite_prefers_abstract_over_description() -> None:
    """Use DataCite abstract when both abstract and description are present."""
    doi = "10.1234/example-doi"
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json={
            "data": {
                "attributes": {
                    "publisher": "Environmental Data Initiative",
                    "descriptions": [
                        {"descriptionType": "Other", "description": "Fallback description"},
                        {"descriptionType": "Abstract", "description": "Primary abstract"},
                    ],
                }
            }
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Primary abstract"
    assert result.context_type == "abstract"
    assert result.source == "datacite"
    assert result.provider == "edi"
    assert result.attempts == ["datacite"]


@responses.activate
def test_edi_provider_api_abstract_wins() -> None:
    """Resolve EDI DOI to EML metadata and extract abstract text first."""
    doi = "10.6073/pasta/abc123"
    metadata_url = "https://pasta.lternet.edu/package/metadata/eml/edi/123/1"
    responses.add(
        responses.GET,
        f"{EDI_DOI_API}/doi:{doi}",
        body=f"{metadata_url}\n",
        content_type="text/plain",
    )
    responses.add(
        responses.GET,
        metadata_url,
        body=(
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<eml:eml xmlns:eml='https://eml.ecoinformatics.org/eml-2.2.0'>"
            "<dataset><abstract><para>EDI abstract text.</para></abstract></dataset>"
            "</eml:eml>"
        ),
        content_type="application/xml",
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "EDI abstract text."
    assert result.context_type == "abstract"
    assert result.source == "edi"
    assert result.provider == "edi"
    assert result.attempts == ["edi"]


@responses.activate
def test_zenodo_provider_api_is_used_first() -> None:
    """For Zenodo-prefix DOIs, query Zenodo API before generic fallbacks."""
    doi = "10.5281/zenodo.7406532"
    responses.add(
        responses.GET,
        ZENODO_API,
        json={
            "hits": {
                "hits": [
                    {
                        "metadata": {
                            "description": "<p>Zenodo dataset description.</p>",
                        }
                    }
                ]
            }
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Zenodo dataset description."
    assert result.context_type == "description"
    assert result.source == "zenodo"
    assert result.provider == "zenodo"
    assert result.attempts == ["zenodo"]


@responses.activate
def test_provider_miss_falls_back_to_datacite() -> None:
    """Fall back to DataCite when provider-specific API has no context."""
    doi = "10.5281/zenodo.7406532"
    responses.add(responses.GET, ZENODO_API, json={"hits": {"hits": []}})
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json={
            "data": {
                "attributes": {
                    "publisher": "Zenodo",
                    "descriptions": [
                        {
                            "descriptionType": "Other",
                            "description": "Datacite description fallback",
                        }
                    ],
                }
            }
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Datacite description fallback"
    assert result.context_type == "description"
    assert result.source == "datacite"
    assert result.attempts == ["zenodo", "datacite"]


@responses.activate
def test_ess_dive_api_abstract_wins() -> None:
    """Return ESS-DIVE abstract immediately when provider API resolves it."""
    doi = "10.15485/1729719"
    responses.add(
        responses.GET,
        ESS_DIVE_API,
        json={"data": [{"abstract": "ESS-DIVE abstract text"}]},
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "ESS-DIVE abstract text"
    assert result.context_type == "abstract"
    assert result.source == "ess-dive"
    assert result.provider == "ess-dive"
    assert result.attempts == ["ess-dive"]


@responses.activate
def test_all_sources_miss_returns_clean_error() -> None:
    """Return a clean no-context error with full attempted-source trace."""
    doi = "10.25982/109073.30/1895615"
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json={"data": {"attributes": {"descriptions": []}}},
    )
    responses.add(responses.GET, f"{CROSSREF_API}/{doi}", json={"message": {}})
    responses.add(
        responses.GET,
        f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
        json={},
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context is None
    assert result.error == "No description or abstract found in any source"
    assert result.provider == "kbase"
    assert result.attempts == ["datacite", "crossref", "content_negotiation"]
