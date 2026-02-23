"""Unit tests for DOI context ingestion and source-waterfall behavior.

This module focuses on three test categories:
1. Fixture-driven routing checks, using ``tests/fixtures/doi_test_cases.json``
   to confirm provider DOIs are routed to the expected dedicated resolver.
2. Provider-specific resolver behavior, with mocked API responses for sources
   like EDI, EMSL, ESS-DIVE, JGI, KBase, MassIVE, CyVerse, Figshare, and Zenodo.
3. Waterfall fallback/error behavior, ensuring clean fallback to generic DOI
   sources (DataCite/Crossref/content negotiation) and clear errors on misses.

All tests in this file mock HTTP with ``responses`` and avoid live API calls.
"""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from nmdc_metadata_suggestor.doi_ingestion.main import (
    CROSSREF_API,
    CYVERSE_METADATA_API,
    CYVERSE_METADATA_SEARCH_API,
    DATACITE_API,
    DATAONE_CN_SOLR_API,
    DOI_CONTENT_NEGOTIATION_API,
    EDI_DOI_API,
    EMSL_PROJECTS_API,
    ESS_DIVE_API,
    FIGSHARE_API,
    FIGSHARE_COLLECTIONS_API,
    JGI_SEARCH_API,
    KBASE_SEARCH_API,
    KBASE_WORKSPACE_API,
    PROXI_DATASETS_API,
    ZENODO_API,
    get_doi_description_or_abstract,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "doi_test_cases.json"
REAL_WORLD_SOURCE_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "doi_resolver_source_examples.json"
)

_FIXTURE_PROVIDER_TO_SOURCE: dict[str, str] = {
    "ESS-DIVE": "ess-dive",
    "JGI": "jgi",
    "MassIVE": "massive",
    "Zenodo": "zenodo",
    "Figshare": "figshare",
    "EDI (Environmental Data Initiative)": "edi",
    "KBase": "kbase",
    "EMSL/OSTI": "emsl",
}


def _load_fixture_provider_cases() -> list[tuple[str, str]]:
    """Return (doi, source) pairs mapped from curated fixture provider names."""
    with open(FIXTURE_PATH) as f:
        test_cases = json.load(f)["test_cases"]

    cases: list[tuple[str, str]] = []
    for case in test_cases:
        if not case.get("valid"):
            continue
        provider = case.get("provider")
        doi = case.get("doi")
        if not isinstance(provider, str) or not isinstance(doi, str):
            continue
        source = _FIXTURE_PROVIDER_TO_SOURCE.get(provider)
        if source is None:
            continue
        cases.append((doi, source))
    return cases


FIXTURE_PROVIDER_CASES = _load_fixture_provider_cases()


def _load_real_world_source_cases() -> list[dict[str, str]]:
    """Load curated real-world DOI cases grouped by resolver source."""
    with open(REAL_WORLD_SOURCE_FIXTURE_PATH) as f:
        cases = json.load(f)["cases"]

    normalized: list[dict[str, str]] = []
    for case in cases:
        source = case.get("source")
        doi = case.get("doi")
        route = case.get("route")
        if not isinstance(source, str) or not isinstance(doi, str):
            continue
        if not isinstance(route, str):
            route = "default"
        normalized.append({"source": source, "doi": doi, "route": route})
    return normalized


REAL_WORLD_SOURCE_CASES = _load_real_world_source_cases()
integration = pytest.mark.integration

KNOWN_LIVE_NO_CONTEXT_DOIS = {
    "10.25585/1488274",
    "10.7946/p22k7v",
}


def _select_live_success_cases(cases: list[dict[str, str]]) -> list[dict[str, str]]:
    """Pick one likely-success live example per resolver source from fixture cases."""
    selected: dict[str, dict[str, str]] = {}
    for case in cases:
        doi = case["doi"]
        source = case["source"]
        if doi in KNOWN_LIVE_NO_CONTEXT_DOIS:
            continue
        if source not in selected:
            selected[source] = case
    return list(selected.values())


LIVE_SUCCESS_CASES = _select_live_success_cases(REAL_WORLD_SOURCE_CASES)
LIVE_KNOWN_NO_CONTEXT_CASES = [
    case for case in REAL_WORLD_SOURCE_CASES if case["doi"] in KNOWN_LIVE_NO_CONTEXT_DOIS
]


def _mock_provider_resolver_hit(source: str, doi: str) -> None:
    """Register minimal mocks that make one provider resolver return context."""
    if source == "edi":
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
                "<dataset><abstract><para>Fixture EDI abstract.</para></abstract></dataset>"
                "</eml:eml>"
            ),
            content_type="application/xml",
        )
        return

    if source == "emsl":
        match = re.search(r"\.proj\.\d{4}\.(\d+)(?:/|$)", doi)
        project_id = match.group(1) if match is not None else "48483"
        responses.add(
            responses.GET,
            f"{EMSL_PROJECTS_API}/{project_id}",
            json={"award_doi": doi, "abstract": "Fixture EMSL abstract."},
        )
        return

    if source == "ess-dive":
        responses.add(
            responses.GET,
            ESS_DIVE_API,
            json={"data": [{"abstract": "Fixture ESS-DIVE abstract."}]},
        )
        return

    if source == "figshare":
        responses.add(
            responses.GET,
            FIGSHARE_API,
            json=[{"description": "Fixture Figshare description."}],
        )
        return

    if source == "jgi":
        responses.add(
            responses.GET,
            JGI_SEARCH_API,
            json={"proposals": [{"doi": doi, "abstract": "Fixture JGI abstract."}]},
        )
        return

    if source == "kbase":
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
                            "doc": {
                                "cells": [
                                    {
                                        "desc": (
                                            f"Citation: doi:{doi}. "
                                            "Fixture KBase narrative context."
                                        )
                                    }
                                ]
                            }
                        }
                    ],
                },
            },
        )
        return

    if source == "massive":
        accession = "MSV000093271"
        responses.add(
            responses.GET,
            f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
            status=302,
            headers={"Location": f"https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={accession}"},
        )
        responses.add(
            responses.GET,
            f"{PROXI_DATASETS_API}/{accession}",
            json={"description": "Fixture MassIVE dataset description."},
        )
        return

    if source == "zenodo":
        responses.add(
            responses.GET,
            ZENODO_API,
            json={"hits": {"hits": [{"metadata": {"description": "Fixture Zenodo description."}}]}},
        )
        return

    if source == "cyverse":
        target_id = "7cfd91a9-2e07-4224-9c5b-26a04ce24122"
        responses.add(
            responses.POST,
            CYVERSE_METADATA_SEARCH_API,
            json={"avus": [{"attr": "dc.identifier.doi", "value": doi, "target_id": target_id}]},
        )
        responses.add(
            responses.GET,
            CYVERSE_METADATA_API,
            json={
                "avus": [
                    {
                        "attr": "dc.description.abstract",
                        "value": "Fixture CyVerse abstract.",
                        "target_id": target_id,
                    }
                ]
            },
        )
        return

    raise AssertionError(f"Unsupported fixture-mapped source: {source}")


@pytest.mark.parametrize(
    "doi,expected_source",
    FIXTURE_PROVIDER_CASES,
    ids=[f"{source}:{doi}" for doi, source in FIXTURE_PROVIDER_CASES],
)
@responses.activate
def test_fixture_provider_dois_use_expected_resolvers(doi: str, expected_source: str) -> None:
    """Fixture provider DOIs should resolve via their specific source first."""
    _mock_provider_resolver_hit(expected_source, doi)

    result = get_doi_description_or_abstract(doi)
    assert result.context is not None
    assert result.source == expected_source
    assert result.provider == expected_source
    assert result.attempts == [expected_source]


def test_real_world_source_fixture_coverage() -> None:
    """Fixture includes all dedicated DOI resolver sources with multiple cases."""
    expected_sources = {
        "edi",
        "emsl",
        "ess-dive",
        "figshare",
        "jgi",
        "kbase",
        "massive",
        "zenodo",
        "cyverse",
    }
    fixture_sources = {case["source"] for case in REAL_WORLD_SOURCE_CASES}
    assert expected_sources <= fixture_sources

    for source in expected_sources:
        count = sum(1 for case in REAL_WORLD_SOURCE_CASES if case["source"] == source)
        assert count >= 2, f"Expected at least two fixture cases for source '{source}'"

    for case in REAL_WORLD_SOURCE_CASES:
        assert case["doi"].startswith("10."), f"Expected DOI syntax for fixture case: {case}"
        assert case["route"] in {"default", "explicit"}


@pytest.mark.parametrize(
    "case",
    REAL_WORLD_SOURCE_CASES,
    ids=[f"{case['source']}:{case['doi']}" for case in REAL_WORLD_SOURCE_CASES],
)
@responses.activate
def test_real_world_source_cases_use_expected_resolver(case: dict[str, str]) -> None:
    """Real-world source fixture cases route into their intended resolver."""
    source = case["source"]
    doi = case["doi"]
    route = case["route"]

    _mock_provider_resolver_hit(source, doi)

    if route == "explicit":
        result = get_doi_description_or_abstract(doi, sources=[source])
    else:
        result = get_doi_description_or_abstract(doi)

    assert result.context is not None
    assert result.source == source
    assert result.attempts == [source]

    if route == "default":
        assert result.provider == source


def test_invalid_doi_rejected_without_attempts() -> None:
    """Reject malformed DOI input before attempting any source."""
    result = get_doi_description_or_abstract("not-a-doi")
    assert result.context is None
    assert result.attempts == []
    assert result.error is not None
    error = result.error
    assert "Invalid DOI" in error


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
def test_figshare_article_detail_used_when_summary_lacks_description() -> None:
    """Fetch Figshare article detail by id when DOI search payload has no description."""
    doi = "10.6084/m9.figshare.26988151"
    responses.add(
        responses.GET,
        FIGSHARE_API,
        json=[
            {
                "id": 26988151,
                "title": "Summary title only",
                "doi": "10.6084/m9.figshare.26988151.v1",
            }
        ],
    )
    responses.add(
        responses.GET,
        f"{FIGSHARE_API}/26988151",
        json={"description": "Figshare detail description text."},
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Figshare detail description text."
    assert result.context_type == "description"
    assert result.source == "figshare"
    assert result.provider == "figshare"
    assert result.attempts == ["figshare"]


@responses.activate
def test_figshare_collection_doi_uses_collection_api() -> None:
    """Handle Figshare collection DOI by querying collection endpoints."""
    doi = "10.6084/m9.figshare.c.7373842"
    responses.add(
        responses.GET,
        FIGSHARE_API,
        json=[],
    )
    responses.add(
        responses.GET,
        FIGSHARE_COLLECTIONS_API,
        json=[{"id": 7373842, "title": "Collection summary title"}],
    )
    responses.add(
        responses.GET,
        f"{FIGSHARE_COLLECTIONS_API}/7373842",
        json={"description": "Figshare collection description text."},
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Figshare collection description text."
    assert result.context_type == "description"
    assert result.source == "figshare"
    assert result.provider == "figshare"
    assert result.attempts == ["figshare"]


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
    context = result.context
    assert context is not None
    assert "Please cite this dataset" in context
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
        responses.POST,
        KBASE_WORKSPACE_API,
        json={"error": {"message": "Object not found"}},
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json={"error": {"message": "Object not found"}},
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json={"error": {"message": "Object not found"}},
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
def test_kbase_workspace_narrative_name_used_when_search_misses() -> None:
    """Use KBase workspace object metadata when narrative search returns no hits."""
    doi = "10.25982/156785.278/2588866"
    responses.add(
        responses.POST,
        KBASE_SEARCH_API,
        json={"jsonrpc": "2.0", "id": "1", "result": {"count": 0, "hits": [], "aggregations": {}}},
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json={
            "version": "1.1",
            "result": [
                {
                    "infos": [
                        [
                            278,
                            "Narrative.1694710832822",
                            "KBaseNarrative.Narrative-4.0",
                            "2023-09-14T00:00:00+0000",
                            1,
                            "pwdna",
                            156785,
                            "pwdna:narrative_1694710832822",
                            "checksum",
                            1,
                            {
                                "description": "",
                                "name": "Produced Water DNA Database",
                                "ws_name": "pwdna:narrative_1694710832822",
                            },
                        ]
                    ]
                }
            ],
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Produced Water DNA Database"
    assert result.context_type == "description"
    assert result.source == "kbase"
    assert result.provider == "kbase"
    assert result.attempts == ["kbase"]


@responses.activate
def test_kbase_workspace_object_metadata_summary_used_when_name_is_technical() -> None:
    """Build useful context from workspace metadata when object name is technical."""
    doi = "10.25982/200311.171/3014786"
    responses.add(
        responses.POST,
        KBASE_SEARCH_API,
        json={"jsonrpc": "2.0", "id": "1", "result": {"count": 0, "hits": [], "aggregations": {}}},
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json={"error": {"message": "Object not found"}},
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json={
            "version": "1.1",
            "result": [
                {
                    "infos": [
                        [
                            171,
                            "22_output__TCS_genomes",
                            "KBaseGenomeAnnotations.Assembly-5.1",
                            "2024-12-10T00:00:00+0000",
                            1,
                            "schatun",
                            200311,
                            "ws",
                            "checksum",
                            1,
                            {
                                "Size": "10721312",
                                "N Contigs": "96",
                                "GC content": "0.57327",
                            },
                        ]
                    ]
                }
            ],
        },
    )

    result = get_doi_description_or_abstract(doi)
    context = result.context
    assert context is not None
    assert "22 output TCS genomes" in context
    assert "type KBaseGenomeAnnotations.Assembly-5.1" in context
    assert "Size=10721312" in context
    assert "N Contigs=96" in context
    assert result.context_type == "description"
    assert result.source == "kbase"
    assert result.provider == "kbase"
    assert result.attempts == ["kbase"]


@responses.activate
def test_massive_provider_api_description_wins() -> None:
    """Resolve MassIVE DOI via DOI redirect plus ProteomeCentral dataset details."""
    doi = "10.25345/C5FX7482Q"
    accession = "MSV000093271"
    responses.add(
        responses.GET,
        f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
        status=302,
        headers={"Location": f"https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={accession}"},
    )
    responses.add(
        responses.GET,
        f"{PROXI_DATASETS_API}/{accession}",
        json={
            "title": "MassIVE dataset title",
            "description": "MassIVE dataset description text.",
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "MassIVE dataset description text."
    assert result.context_type == "description"
    assert result.source == "massive"
    assert result.provider == "massive"
    assert result.attempts == ["massive"]


@responses.activate
def test_massive_proxi_miss_falls_back_to_datacite() -> None:
    """If MassIVE resolver cannot retrieve dataset detail, continue waterfall."""
    doi = "10.25345/C5FX7482Q"
    accession = "MSV000093271"
    responses.add(
        responses.GET,
        f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
        status=302,
        headers={"Location": f"https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={accession}"},
    )
    responses.add(
        responses.GET,
        f"{PROXI_DATASETS_API}/{accession}",
        json={"status": "ERROR", "description": "Identifier not reserved"},
    )
    responses.add(
        responses.GET,
        f"{PROXI_DATASETS_API}",
        json={"datasets": [], "status": {"status": "OK"}},
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json={
            "data": {
                "attributes": {
                    "publisher": "MassIVE",
                    "descriptions": [
                        {
                            "descriptionType": "Abstract",
                            "description": "DataCite fallback for MassIVE DOI",
                        }
                    ],
                }
            }
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "DataCite fallback for MassIVE DOI"
    assert result.context_type == "abstract"
    assert result.source == "datacite"
    assert result.provider == "massive"
    assert result.attempts == ["massive", "datacite"]


@responses.activate
def test_massive_uses_datacite_title_context_when_proxi_fails() -> None:
    """Use DataCite subtitle/title context within MassIVE resolver as a fallback."""
    doi = "10.25345/c5kk94s01"
    accession = "MSV000100938"
    responses.add(
        responses.GET,
        f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
        status=302,
        headers={"Location": f"https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={accession}"},
    )
    responses.add(
        responses.GET,
        f"{PROXI_DATASETS_API}/{accession}",
        json={"status": "ERROR", "description": "Identifier not reserved"},
    )
    responses.add(
        responses.GET,
        PROXI_DATASETS_API,
        json={"datasets": [], "status": {"status": "OK"}},
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json={
            "data": {
                "attributes": {
                    "titles": [
                        {"title": "MassIVE dataset title"},
                        {
                            "titleType": "Subtitle",
                            "title": "Detailed MassIVE subtitle context from DataCite.",
                        },
                    ]
                }
            }
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Detailed MassIVE subtitle context from DataCite."
    assert result.context_type == "description"
    assert result.source == "massive"
    assert result.provider == "massive"
    assert result.attempts == ["massive"]


@responses.activate
def test_cyverse_provider_api_abstract_wins() -> None:
    """Resolve CyVerse DOI via Terrain metadata and return abstract text."""
    doi = "10.17504/cyverse.dataset.12345"
    target_id = "7cfd91a9-2e07-4224-9c5b-26a04ce24122"
    responses.add(
        responses.POST,
        CYVERSE_METADATA_SEARCH_API,
        json={"avus": [{"attr": "dc.identifier.doi", "value": doi, "target_id": target_id}]},
    )
    responses.add(
        responses.GET,
        CYVERSE_METADATA_API,
        json={
            "avus": [
                {
                    "attr": "dc.description.abstract",
                    "value": "CyVerse dataset abstract text.",
                    "target_id": target_id,
                }
            ]
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "CyVerse dataset abstract text."
    assert result.context_type == "abstract"
    assert result.source == "cyverse"
    assert result.provider == "cyverse"
    assert result.attempts == ["cyverse"]


@responses.activate
def test_cyverse_miss_falls_back_to_datacite() -> None:
    """If CyVerse API has no matching metadata, continue waterfall to DataCite."""
    doi = "10.17504/cyverse.dataset.67890"
    responses.add(
        responses.POST,
        CYVERSE_METADATA_SEARCH_API,
        json={"avus": []},
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json={
            "data": {
                "attributes": {
                    "publisher": "CyVerse",
                    "descriptions": [
                        {
                            "descriptionType": "Abstract",
                            "description": "DataCite fallback for CyVerse DOI",
                        }
                    ],
                }
            }
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "DataCite fallback for CyVerse DOI"
    assert result.context_type == "abstract"
    assert result.source == "datacite"
    assert result.provider == "cyverse"
    assert result.attempts == ["cyverse", "datacite"]


@responses.activate
def test_cyverse_explicit_source_no_context_returns_clean_error() -> None:
    """Explicit CyVerse-only lookup should fail cleanly when metadata has no context."""
    doi = "10.7946/p22k7v"
    responses.add(
        responses.POST,
        CYVERSE_METADATA_SEARCH_API,
        json={"avus": []},
    )

    result = get_doi_description_or_abstract(doi, sources=["cyverse"])
    assert result.context is None
    assert result.source is None
    assert result.error == "No description or abstract found in any source"
    assert result.attempts == ["cyverse"]


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
def test_zenodo_concept_doi_query_prefers_zenodo_over_datacite() -> None:
    """Resolve Zenodo concept DOI using conceptdoi-aware query instead of fallback."""
    doi = "10.5281/zenodo.7406532"

    def zenodo_callback(request: responses.Call) -> tuple[int, dict[str, str], str]:
        parsed_qs = parse_qs(urlparse(request.url).query)
        query = parsed_qs.get("q", [""])[0]
        if 'conceptdoi:"10.5281/zenodo.7406532"' in query:
            body = {
                "hits": {
                    "hits": [
                        {
                            "doi": "10.5281/zenodo.15328215",
                            "conceptdoi": "10.5281/zenodo.7406532",
                            "metadata": {
                                "doi": "10.5281/zenodo.15328215",
                                "description": "Zenodo concept DOI description text.",
                            },
                        }
                    ]
                }
            }
            return 200, {"Content-Type": "application/json"}, json.dumps(body)
        return 200, {"Content-Type": "application/json"}, json.dumps({"hits": {"hits": []}})

    responses.add_callback(
        responses.GET,
        ZENODO_API,
        callback=zenodo_callback,
        content_type="application/json",
    )
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
                            "description": "Datacite fallback should not be used",
                        }
                    ],
                }
            }
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Zenodo concept DOI description text."
    assert result.context_type == "description"
    assert result.source == "zenodo"
    assert result.provider == "zenodo"
    assert result.attempts == ["zenodo"]


@responses.activate
def test_zenodo_prefers_hit_matching_requested_doi() -> None:
    """Choose matching Zenodo hit when mixed search results are returned."""
    doi = "10.5281/zenodo.18746325"
    responses.add(
        responses.GET,
        ZENODO_API,
        json={
            "hits": {
                "hits": [
                    {
                        "doi": "10.5281/zenodo.11111111",
                        "conceptdoi": "10.5281/zenodo.11111110",
                        "metadata": {"description": "Wrong Zenodo hit"},
                    },
                    {
                        "doi": "10.5281/zenodo.18746326",
                        "conceptdoi": "10.5281/zenodo.18746325",
                        "metadata": {
                            "doi": "10.5281/zenodo.18746326",
                            "description": "Matching Zenodo concept DOI description.",
                        },
                    },
                ]
            }
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Matching Zenodo concept DOI description."
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
def test_ess_dive_api_401_uses_dataone_fallback() -> None:
    """Use DataONE ESS_DIVE index when ESS-DIVE packages API is unauthorized."""
    doi = "10.15485/1729719"
    responses.add(responses.GET, ESS_DIVE_API, status=401, json={"detail": "Unauthorized"})
    responses.add(
        responses.GET,
        DATAONE_CN_SOLR_API,
        body=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<response><result name='response' numFound='1' start='0'>"
            "<doc>"
            "<str name='id'>ess-dive-abc-20260101</str>"
            "<str name='seriesId'>doi:10.15485/1729719</str>"
            "<str name='datasource'>urn:node:ESS_DIVE</str>"
            "<arr name='abstract'><str>ESS-DIVE DataONE abstract text.</str></arr>"
            "</doc>"
            "</result></response>"
        ),
        content_type="application/xml",
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "ESS-DIVE DataONE abstract text."
    assert result.context_type == "abstract"
    assert result.source == "ess-dive"
    assert result.provider == "ess-dive"
    assert result.attempts == ["ess-dive"]


@responses.activate
def test_ess_dive_dataone_query_tries_uppercase_variant_for_wtr_dois() -> None:
    """Retry DataONE query with uppercase DOI variant for WTR-style series IDs."""
    doi = "10.21952/wtr/1573029"
    responses.add(responses.GET, ESS_DIVE_API, status=401, json={"detail": "Unauthorized"})

    empty_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<response><result name='response' numFound='0' start='0'></result></response>"
    )
    hit_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<response><result name='response' numFound='1' start='0'>"
        "<doc>"
        "<str name='id'>ess-dive-wtr-20260101</str>"
        "<str name='seriesId'>doi:10.21952/WTR/1573029</str>"
        "<str name='datasource'>urn:node:ESS_DIVE</str>"
        "<arr name='abstract'><str>ESS-DIVE WTR abstract text.</str></arr>"
        "</doc>"
        "</result></response>"
    )

    def dataone_callback(request: responses.Call) -> tuple[int, dict[str, str], str]:
        query = parse_qs(urlparse(request.url).query).get("q", [""])[0]
        if "10.21952/wtr/1573029" in query:
            return 200, {"Content-Type": "application/xml"}, empty_xml
        if "10.21952/WTR/1573029" in query:
            return 200, {"Content-Type": "application/xml"}, hit_xml
        return 200, {"Content-Type": "application/xml"}, empty_xml

    responses.add_callback(
        responses.GET,
        DATAONE_CN_SOLR_API,
        callback=dataone_callback,
        content_type="application/xml",
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "ESS-DIVE WTR abstract text."
    assert result.context_type == "abstract"
    assert result.source == "ess-dive"
    assert result.provider == "ess-dive"
    assert result.attempts == ["ess-dive"]


@responses.activate
def test_ess_dive_miss_falls_back_to_datacite() -> None:
    """If ESS-DIVE API and DataONE miss, continue waterfall to DataCite."""
    doi = "10.15485/1729719"
    responses.add(responses.GET, ESS_DIVE_API, status=401, json={"detail": "Unauthorized"})
    responses.add(
        responses.GET,
        DATAONE_CN_SOLR_API,
        body=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<response><result name='response' numFound='0' start='0'></result></response>"
        ),
        content_type="application/xml",
    )
    responses.add(
        responses.GET,
        DATAONE_CN_SOLR_API,
        body=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<response><result name='response' numFound='0' start='0'></result></response>"
        ),
        content_type="application/xml",
    )
    responses.add(
        responses.GET,
        DATAONE_CN_SOLR_API,
        body=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<response><result name='response' numFound='0' start='0'></result></response>"
        ),
        content_type="application/xml",
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json={
            "data": {
                "attributes": {
                    "publisher": "ESS-DIVE",
                    "descriptions": [
                        {
                            "descriptionType": "Abstract",
                            "description": "DataCite fallback for ESS-DIVE DOI",
                        }
                    ],
                }
            }
        },
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "DataCite fallback for ESS-DIVE DOI"
    assert result.context_type == "abstract"
    assert result.source == "datacite"
    assert result.provider == "ess-dive"
    assert result.attempts == ["ess-dive", "datacite"]


@responses.activate
def test_jgi_live_failure_pattern_returns_clean_no_context() -> None:
    """If JGI and all fallbacks miss, return clean error with provider-aware attempts."""
    doi = "10.25585/1488274"
    responses.add(
        responses.GET,
        JGI_SEARCH_API,
        json={"proposals": [], "organisms": []},
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
    assert result.provider == "jgi"
    assert result.error == "No description or abstract found in any source"
    assert result.attempts == ["jgi", "datacite", "crossref", "content_negotiation"]


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
        responses.POST,
        KBASE_WORKSPACE_API,
        json={"error": {"message": "Object not found"}},
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json={"error": {"message": "Object not found"}},
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json={"error": {"message": "Object not found"}},
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


# ---------------------------------------------------------------------------
# Integration tests — hit real DOI/provider APIs, skip in CI by default
# ---------------------------------------------------------------------------


@integration
@pytest.mark.parametrize(
    "case",
    LIVE_SUCCESS_CASES,
    ids=[f"{case['source']}:{case['doi']}" for case in LIVE_SUCCESS_CASES],
)
def test_live_source_example_returns_context(case: dict[str, str]) -> None:
    """Each source-routed live DOI should return context from provider API or fallback."""
    doi = case["doi"]
    source = case["source"]
    route = case["route"]

    if route == "explicit":
        result = get_doi_description_or_abstract(doi, sources=[source])
        assert result.attempts == [source]
    else:
        result = get_doi_description_or_abstract(doi)
        assert result.attempts[0] == source
        assert result.provider == source

    assert result.context is not None, f"Expected context for {doi}; got error={result.error!r}"
    assert result.source is not None
    source_used = result.source
    assert source_used in result.attempts
    assert result.context_type in {"abstract", "description"}


@integration
@pytest.mark.parametrize(
    "case",
    LIVE_KNOWN_NO_CONTEXT_CASES,
    ids=[f"{case['source']}:{case['doi']}" for case in LIVE_KNOWN_NO_CONTEXT_CASES],
)
def test_live_known_no_context_cases_fail_cleanly(case: dict[str, str]) -> None:
    """Known live no-context DOIs should fail cleanly with explicit attempts."""
    doi = case["doi"]
    source = case["source"]
    route = case["route"]

    if route == "explicit":
        result = get_doi_description_or_abstract(doi, sources=[source])
        assert result.attempts == [source]
    else:
        result = get_doi_description_or_abstract(doi)
        assert result.attempts[0] == source

    assert result.context is None
    assert result.error == "No description or abstract found in any source"
