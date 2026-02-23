"""Tests for DOI description/abstract ingestion waterfall."""

import responses

from nmdc_metadata_suggestor.doi_ingestion.main import (
    CROSSREF_API,
    DATACITE_API,
    DOI_CONTENT_NEGOTIATION_API,
    EDI_DOI_API,
    EMSL_PROJECTS_API,
    ESS_DIVE_API,
    JGI_SEARCH_API,
    KBASE_SEARCH_API,
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
def test_emsl_provider_api_abstract_wins() -> None:
    """Resolve EMSL award DOI to project details and return abstract text."""
    doi = "10.46936/jejc.proj.2014.48483/60005501"
    responses.add(
        responses.GET,
        f"{EMSL_PROJECTS_API}/48483",
        json={
            "award_doi": doi,
            "abstract": "EMSL project abstract text.",
            "title": "Fallback title",
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "EMSL project abstract text."
    assert result.context_type == "abstract"
    assert result.source == "emsl"
    assert result.provider == "emsl"
    assert result.attempts == ["emsl"]


@responses.activate
def test_emsl_mismatch_falls_back_to_datacite() -> None:
    """If EMSL project lookup DOI does not match, continue waterfall."""
    doi = "10.46936/jejc.proj.2014.48483/60005501"
    responses.add(
        responses.GET,
        f"{EMSL_PROJECTS_API}/48483",
        json={"award_doi": "10.46936/jejc.proj.2014.99999/60000000", "abstract": "Wrong project"},
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json={
            "data": {
                "attributes": {
                    "publisher": "EMSL",
                    "descriptions": [
                        {
                            "descriptionType": "Abstract",
                            "description": "DataCite fallback abstract",
                        }
                    ],
                }
            }
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "DataCite fallback abstract"
    assert result.context_type == "abstract"
    assert result.source == "datacite"
    assert result.provider == "emsl"
    assert result.attempts == ["emsl", "datacite"]


@responses.activate
def test_jgi_provider_api_lay_description_wins() -> None:
    """Resolve JGI DOI via project search and return lay description."""
    doi = "10.25585/1487763"
    responses.add(
        responses.GET,
        JGI_SEARCH_API,
        json={
            "proposals": [
                {
                    "doi": doi,
                    "lay_description": "JGI lay description text.",
                    "title": "Fallback title",
                }
            ],
            "organisms": [],
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "JGI lay description text."
    assert result.context_type == "description"
    assert result.source == "jgi"
    assert result.provider == "jgi"
    assert result.attempts == ["jgi"]


@responses.activate
def test_jgi_mismatch_falls_back_to_datacite() -> None:
    """If JGI response DOI does not match, continue waterfall to DataCite."""
    doi = "10.25585/1487763"
    responses.add(
        responses.GET,
        JGI_SEARCH_API,
        json={
            "proposals": [{"doi": "10.25585/9999999", "lay_description": "Wrong record"}],
            "organisms": [],
        },
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json={
            "data": {
                "attributes": {
                    "publisher": "JGI",
                    "descriptions": [
                        {
                            "descriptionType": "Abstract",
                            "description": "DataCite fallback for JGI DOI",
                        }
                    ],
                }
            }
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "DataCite fallback for JGI DOI"
    assert result.context_type == "abstract"
    assert result.source == "datacite"
    assert result.provider == "jgi"
    assert result.attempts == ["jgi", "datacite"]


@responses.activate
def test_kbase_provider_api_description_wins() -> None:
    """Resolve KBase DOI via narrative search and return narrative description text."""
    doi = "10.25982/109073.30/1895615"
    responses.add(
        responses.POST,
        KBASE_SEARCH_API,
        json={
            "jsonrpc": "2.0",
            "id": "1",
            "result": {
                "count": 1,
                "hits": [
                    {
                        "index": "narrative_2",
                        "id": "WS::109073:1",
                        "doc": {
                            "narrative_title": "KBase Narrative Title",
                            "cells": [
                                {"desc": "Small intro cell."},
                                {
                                    "desc": (
                                        "Please cite this dataset as "
                                        "doi:10.25982/109073.30/1895615. "
                                        "This narrative describes sample collection and processing."
                                    )
                                },
                            ],
                            "creation_date": "2022-02-14T17:53:04+0000",
                        },
                    }
                ],
                "search_time": 12,
                "aggregations": {},
            },
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert "Please cite this dataset" in result.context
    assert result.context_type == "description"
    assert result.source == "kbase"
    assert result.provider == "kbase"
    assert result.attempts == ["kbase"]


@responses.activate
def test_kbase_miss_falls_back_to_datacite() -> None:
    """If KBase resolver finds no matching narratives, continue waterfall."""
    doi = "10.25982/109073.30/1895615"
    responses.add(
        responses.POST,
        KBASE_SEARCH_API,
        json={"jsonrpc": "2.0", "id": "1", "result": {"count": 0, "hits": [], "aggregations": {}}},
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json={
            "data": {
                "attributes": {
                    "publisher": "KBase",
                    "descriptions": [
                        {
                            "descriptionType": "Abstract",
                            "description": "DataCite fallback for KBase DOI",
                        }
                    ],
                }
            }
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "DataCite fallback for KBase DOI"
    assert result.context_type == "abstract"
    assert result.source == "datacite"
    assert result.provider == "kbase"
    assert result.attempts == ["kbase", "datacite"]


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
        responses.POST,
        KBASE_SEARCH_API,
        json={"jsonrpc": "2.0", "id": "1", "result": {"count": 0, "hits": [], "aggregations": {}}},
    )
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
    assert result.attempts == ["kbase", "datacite", "crossref", "content_negotiation"]
