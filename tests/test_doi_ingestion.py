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
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from requests import PreparedRequest

from nmdc_metadata_suggestor.constants import (
    CROSSREF_API_URL as CROSSREF_API,
)
from nmdc_metadata_suggestor.constants import (
    CYVERSE_DATACOMMONS_API,
    CYVERSE_METADATA_API,
    CYVERSE_METADATA_SEARCH_API,
    DATAONE_CN_SOLR_API,
    DOI_CONTENT_NEGOTIATION_API,
    DOI_RESOLVER_URL,
    EDI_DOI_API,
    EMSL_PROJECTS_API,
    ESS_DIVE_API,
    FIGSHARE_API,
    FIGSHARE_COLLECTIONS_API,
    JGI_SEARCH_API,
    KBASE_SEARCH_API,
    KBASE_WORKSPACE_API,
    OPENALEX_API_URL,
    PROXI_DATASETS_API,
    PUBMED_ID_CONVERTER,
    ZENODO_API,
)
from nmdc_metadata_suggestor.constants import (
    DATACITE_API_URL as DATACITE_API,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    DEFAULT_RETRY_ATTEMPTS,
    text_mentions_doi,
)
from nmdc_metadata_suggestor.doi_ingestion.edi import try_edi
from nmdc_metadata_suggestor.doi_ingestion.emsl import try_emsl
from nmdc_metadata_suggestor.doi_ingestion.jgi import try_jgi
from nmdc_metadata_suggestor.doi_ingestion.kbase import try_kbase
from nmdc_metadata_suggestor.doi_ingestion.main import (
    get_doi_description_or_abstract,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "doi_test_cases.json"
REAL_WORLD_SOURCE_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "doi_resolver_source_examples.json"
)
XML_PAYLOAD_FIXTURE_PATH = Path(
    __file__).parent / "fixtures" / "doi_xml_payloads.json"
JSON_PAYLOAD_FIXTURE_PATH = Path(
    __file__).parent / "fixtures" / "doi_response_payloads.json"

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


def _load_xml_payload_fixtures() -> dict[str, str]:
    """Load reusable XML payload fixtures used by resolver tests."""
    with open(XML_PAYLOAD_FIXTURE_PATH) as f:
        payloads = json.load(f)["payloads"]
    return {str(key): str(value) for key, value in payloads.items()}


XML_PAYLOAD_FIXTURES = _load_xml_payload_fixtures()


def _load_json_payload_fixtures() -> dict[str, Any]:
    """Load reusable JSON payload fixtures used by resolver tests."""
    with open(JSON_PAYLOAD_FIXTURE_PATH) as f:
        return json.load(f)["payloads"]


def _replace_payload_placeholders(value: Any, replacements: dict[str, str]) -> Any:
    """Recursively substitute ``{{placeholder}}`` strings in fixture payloads."""
    if isinstance(value, dict):
        return {
            key: _replace_payload_placeholders(inner, replacements) for key, inner in value.items()
        }
    if isinstance(value, list):
        return [_replace_payload_placeholders(inner, replacements) for inner in value]
    if isinstance(value, str):
        rendered = value
        for key, replacement in replacements.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", replacement)
        return rendered
    return value


def _json_payload(name: str, **replacements: str) -> Any:
    """Return a deep-copied JSON fixture payload with optional placeholder substitutions."""
    payload = deepcopy(JSON_PAYLOAD_FIXTURES[name])
    if not replacements:
        return payload
    return _replace_payload_placeholders(payload, replacements)


JSON_PAYLOAD_FIXTURES = _load_json_payload_fixtures()
integration = pytest.mark.integration

KNOWN_LIVE_NO_CONTEXT_DOIS = {
    "10.25585/1488274",
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


def _live_dois_for_source(source: str) -> list[str]:
    """Return curated live DOIs for a resolver source, excluding known no-context DOIs."""
    return [
        case["doi"]
        for case in REAL_WORLD_SOURCE_CASES
        if case["source"] == source and case["doi"] not in KNOWN_LIVE_NO_CONTEXT_DOIS
    ]


def _add_retriable_http_response(
    method: str, url: str, *, status: int, json_payload: object | None = None
) -> None:
    """Register enough mocked responses to satisfy retry attempts for one request."""
    for _ in range(DEFAULT_RETRY_ATTEMPTS):
        if json_payload is None:
            responses.add(method, url, status=status)
        else:
            responses.add(method, url, status=status, json=json_payload)


def _datacite_abstract_payload(text: str, publisher: str = "Test Publisher") -> dict[str, object]:
    """Return a minimal DataCite payload with one abstract description."""
    return {
        "data": {
            "attributes": {
                "publisher": publisher,
                "descriptions": [
                    {
                        "description": text,
                        "descriptionType": "Abstract",
                    }
                ],
            }
        }
    }


def _mock_openalex_miss(doi: str) -> None:
    """Mock OpenAlex call as a clean 'no abstract' miss."""
    responses.add(
        responses.GET,
        f"{OPENALEX_API_URL}/https://doi.org/{doi}",
        json={},
        status=200,
    )


def _mock_pubmed_miss() -> None:
    """Mock PubMed DOI->PMID converter as a clean 'no record' miss."""
    responses.add(
        responses.GET,
        PUBMED_ID_CONVERTER,
        json={"records": []},
        status=200,
    )


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
            body=XML_PAYLOAD_FIXTURES["edi_fixture_abstract_xml"],
            content_type="application/xml",
        )
        return

    if source == "emsl":
        match = re.search(r"\.proj\.\d{4}\.(\d+)(?:/|$)", doi)
        project_id = match.group(1) if match is not None else "48483"
        responses.add(
            responses.GET,
            f"{EMSL_PROJECTS_API}/{project_id}",
            json=_json_payload("fixture_emsl_hit", doi=doi),
        )
        return

    if source == "ess-dive":
        responses.add(
            responses.GET,
            ESS_DIVE_API,
            json=_json_payload("fixture_ess_dive_hit"),
        )
        return

    if source == "figshare":
        responses.add(
            responses.GET,
            FIGSHARE_API,
            json=_json_payload("fixture_figshare_hit"),
        )
        return

    if source == "jgi":
        responses.add(
            responses.GET,
            JGI_SEARCH_API,
            json=_json_payload("fixture_jgi_hit", doi=doi),
        )
        return

    if source == "kbase":
        responses.add(
            responses.POST,
            KBASE_SEARCH_API,
            json=_json_payload("fixture_kbase_hit", doi=doi),
        )
        return

    if source == "massive":
        accession = "MSV000093271"
        responses.add(
            responses.GET,
            f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
            status=302,
            headers={
                "Location": f"https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={accession}"
            },
        )
        responses.add(
            responses.GET,
            f"{PROXI_DATASETS_API}/{accession}",
            json=_json_payload("fixture_massive_detail"),
        )
        return

    if source == "zenodo":
        responses.add(
            responses.GET,
            ZENODO_API,
            json=_json_payload("fixture_zenodo_hit"),
        )
        return

    if source == "cyverse":
        target_id = "7cfd91a9-2e07-4224-9c5b-26a04ce24122"
        responses.add(
            responses.POST,
            CYVERSE_METADATA_SEARCH_API,
            json=_json_payload("fixture_cyverse_search_hit",
                               doi=doi, target_id=target_id),
        )
        responses.add(
            responses.GET,
            CYVERSE_METADATA_API,
            json=_json_payload(
                "fixture_cyverse_metadata_hit", target_id=target_id),
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
        count = sum(
            1 for case in REAL_WORLD_SOURCE_CASES if case["source"] == source)
        assert count >= 2, f"Expected at least two fixture cases for source '{source}'"

    for case in REAL_WORLD_SOURCE_CASES:
        assert case["doi"].startswith(
            "10."), f"Expected DOI syntax for fixture case: {case}"
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
        json=_json_payload("datacite_prefers_abstract"),
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
        json=_json_payload("figshare_summary_article"),
    )
    responses.add(
        responses.GET,
        f"{FIGSHARE_API}/26988151",
        json=_json_payload("figshare_detail_description"),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Figshare detail description text."
    assert result.context_type == "description"
    assert result.source == "figshare"
    assert result.provider == "figshare"
    assert result.attempts == ["figshare"]


@responses.activate
def test_figshare_link_only_falls_back_and_preserves_publication_metadata() -> None:
    """Figshare file and linked publication DOI should survive a later text fallback."""
    doi = "10.6084/m9.figshare.26988151"
    publication_doi = "10.1000/figshare-paper"
    publication_url = "https://example.org/figshare-paper.pdf"
    responses.add(
        responses.GET,
        FIGSHARE_API,
        json=[{"id": 26988151}],
    )
    responses.add(
        responses.GET,
        f"{FIGSHARE_API}/26988151",
        json={
            "files": [
                {
                    "download_url": publication_url,
                    "name": "figshare-paper.pdf",
                    "mimetype": "application/pdf",
                }
            ],
            "resource_doi": publication_doi,
        },
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_datacite_abstract_payload(
            "DataCite fallback after Figshare link-only metadata"),
    )

    result = get_doi_description_or_abstract(doi)

    assert result.context == "DataCite fallback after Figshare link-only metadata"
    assert result.source == "datacite"
    assert result.attempts == ["figshare", "datacite"]
    assert result.publication_urls == [publication_url]
    assert result.publication_dois == [publication_doi]


@responses.activate
def test_figshare_collection_doi_uses_collection_api() -> None:
    """Handle Figshare collection DOI by querying collection endpoints."""
    doi = "10.6084/m9.figshare.c.7373842"
    responses.add(
        responses.GET,
        FIGSHARE_API,
        json=_json_payload("figshare_empty_search"),
    )
    responses.add(
        responses.GET,
        FIGSHARE_COLLECTIONS_API,
        json=_json_payload("figshare_collection_summary"),
    )
    responses.add(
        responses.GET,
        f"{FIGSHARE_COLLECTIONS_API}/7373842",
        json=_json_payload("figshare_collection_detail"),
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
        body=XML_PAYLOAD_FIXTURES["edi_provider_abstract_xml"],
        content_type="application/xml",
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "EDI abstract text."
    assert result.context_type == "abstract"
    assert result.source == "edi"
    assert result.provider == "edi"
    assert result.attempts == ["edi"]


@responses.activate
def test_edi_link_only_falls_back_and_preserves_publication_metadata() -> None:
    """EDI link-only metadata should continue the waterfall and retain publication links."""
    doi = "10.6073/pasta/abc123"
    metadata_url = "https://pasta.lternet.edu/package/metadata/eml/edi/123/1"
    publication_doi = "10.1000/edi-paper"
    publication_url = "https://example.org/edi-paper.pdf"
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
            "<dataset><additionalMetadata><metadata>"
            f"<citation>{publication_url} doi:{publication_doi}</citation>"
            "</metadata></additionalMetadata></dataset></eml:eml>"
        ),
        content_type="application/xml",
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_datacite_abstract_payload(
            "DataCite fallback after EDI link-only metadata"),
    )

    result = get_doi_description_or_abstract(doi)

    assert result.context == "DataCite fallback after EDI link-only metadata"
    assert result.source == "datacite"
    assert result.attempts == ["edi", "datacite"]
    assert result.publication_urls == [publication_url]
    assert result.publication_dois == [publication_doi]


@responses.activate
def test_edi_unsafe_metadata_xml_falls_back_to_datacite() -> None:
    """Reject unsafe EDI XML declarations and continue waterfall."""
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
        body=XML_PAYLOAD_FIXTURES["edi_unsafe_metadata_xml"],
        content_type="application/xml",
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_edi_after_unsafe_xml"),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "DataCite fallback after unsafe EDI XML"
    assert result.context_type == "abstract"
    assert result.source == "datacite"
    assert result.provider == "edi"
    assert result.attempts == ["edi", "datacite"]
    assert "unsafe declarations" in result.source_errors["edi"]


@responses.activate
def test_emsl_provider_api_abstract_wins() -> None:
    """Resolve EMSL award DOI to project details and return abstract text."""
    doi = "10.46936/jejc.proj.2014.48483/60005501"
    responses.add(
        responses.GET,
        f"{EMSL_PROJECTS_API}/48483",
        json=_json_payload("emsl_provider_hit", doi=doi),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "EMSL project abstract text."
    assert result.context_type == "abstract"
    assert result.source == "emsl"
    assert result.provider == "emsl"
    assert result.attempts == ["emsl"]


@responses.activate
def test_emsl_link_only_falls_back_and_preserves_publication_metadata() -> None:
    """EMSL publication links should be preserved when the provider payload has no text."""
    doi = "10.46936/jejc.proj.2014.48483/60005501"
    publication_doi = "10.1000/emsl-paper"
    publication_url = "https://example.org/emsl-paper.pdf"
    responses.add(
        responses.GET,
        f"{EMSL_PROJECTS_API}/48483",
        json={
            "award_doi": doi,
            "publications": [
                {
                    "url": publication_url,
                    "relatedIdentifier": publication_doi,
                    "relation": "isPublishedIn",
                    "resource_type": "journal-article",
                }
            ],
        },
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_datacite_abstract_payload(
            "DataCite fallback after EMSL link-only metadata"),
    )

    result = get_doi_description_or_abstract(doi)

    assert result.context == "DataCite fallback after EMSL link-only metadata"
    assert result.source == "datacite"
    assert result.attempts == ["emsl", "datacite"]
    assert result.publication_urls == [publication_url]
    assert result.publication_dois == [publication_doi]


@responses.activate
def test_emsl_mismatch_falls_back_to_datacite() -> None:
    """If EMSL project lookup DOI does not match, continue waterfall."""
    doi = "10.46936/jejc.proj.2014.48483/60005501"
    responses.add(
        responses.GET,
        f"{EMSL_PROJECTS_API}/48483",
        json=_json_payload("emsl_mismatch_project"),
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_emsl_fallback"),
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
        json=_json_payload("jgi_lay_description_hit", doi=doi),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "JGI lay description text."
    assert result.context_type == "description"
    assert result.source == "jgi"
    assert result.provider == "jgi"
    assert result.attempts == ["jgi"]


@responses.activate
def test_jgi_link_only_falls_back_and_preserves_publication_metadata() -> None:
    """JGI publication link metadata should survive a later DataCite text fallback."""
    doi = "10.25585/1487763"
    publication_doi = "10.1000/jgi-paper"
    publication_url = "https://example.org/jgi-paper.pdf"
    link_only_payload = {
        "proposals": [
            {
                "doi": doi,
                "publications": [
                    {
                        "url": publication_url,
                        "relatedIdentifier": publication_doi,
                        "relation": "isPublishedIn",
                        "resource_type": "journal-article",
                    }
                ],
            }
        ],
        "organisms": [],
    }
    responses.add(responses.GET, JGI_SEARCH_API, json=link_only_payload)
    responses.add(responses.GET, JGI_SEARCH_API, json=link_only_payload)
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_datacite_abstract_payload(
            "DataCite fallback after JGI link-only metadata"),
    )

    result = get_doi_description_or_abstract(doi)

    assert result.context == "DataCite fallback after JGI link-only metadata"
    assert result.source == "datacite"
    assert result.attempts == ["jgi", "datacite"]
    assert result.publication_urls == [publication_url]
    assert result.publication_dois == [publication_doi]


@responses.activate
def test_jgi_mismatch_falls_back_to_datacite() -> None:
    """If JGI response DOI does not match, continue waterfall to DataCite."""
    doi = "10.25585/1487763"
    responses.add(
        responses.GET,
        JGI_SEARCH_API,
        json=_json_payload("jgi_mismatch_payload"),
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_jgi_fallback"),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "DataCite fallback for JGI DOI"
    assert result.context_type == "abstract"
    assert result.source == "datacite"
    assert result.provider == "jgi"
    assert result.attempts == ["jgi", "datacite"]


@responses.activate
def test_jgi_missing_doi_fields_falls_back_to_datacite() -> None:
    """If JGI proposals omit DOI keys, treat as non-match and continue waterfall."""
    doi = "10.25585/1487763"
    responses.add(
        responses.GET,
        JGI_SEARCH_API,
        json=_json_payload("jgi_missing_doi_payload"),
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_jgi_missing_doi_fallback"),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "DataCite fallback for DOI-missing JGI result"
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
        json=_json_payload("kbase_search_hit_description"),
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
def test_kbase_link_only_falls_back_and_preserves_publication_metadata() -> None:
    """KBase workspace publication links should not stop the waterfall before text fallback."""
    doi = "10.25982/156785.278/2588866"
    publication_doi = "10.1000/kbase-paper"
    publication_url = "https://example.org/kbase-paper.pdf"
    responses.add(
        responses.POST,
        KBASE_SEARCH_API,
        json=_json_payload("kbase_search_empty"),
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json={
            "result": [
                {
                    "infos": [
                        [
                            1,
                            "narrative.999",
                            "KBaseNarrative.Narrative-4.0",
                            "2025-01-01T00:00:00+0000",
                            1,
                            "user",
                            1,
                            "workspace",
                            "checksum",
                            1,
                            {
                                "publication_url": publication_url,
                                "citation": f"doi:{publication_doi}",
                            },
                        ]
                    ]
                }
            ]
        },
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_datacite_abstract_payload(
            "DataCite fallback after KBase link-only metadata"),
    )

    result = get_doi_description_or_abstract(doi)

    assert result.context == "DataCite fallback after KBase link-only metadata"
    assert result.source == "datacite"
    assert result.attempts == ["kbase", "datacite"]
    assert result.publication_urls == [publication_url]
    assert result.publication_dois == [publication_doi]


@responses.activate
def test_kbase_miss_falls_back_to_datacite() -> None:
    """If KBase resolver finds no matching narratives, continue waterfall."""
    doi = "10.25982/109073.30/1895615"
    responses.add(
        responses.POST,
        KBASE_SEARCH_API,
        json=_json_payload("kbase_search_empty"),
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json=_json_payload("kbase_workspace_not_found"),
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json=_json_payload("kbase_workspace_not_found"),
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json=_json_payload("kbase_workspace_not_found"),
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_kbase_fallback"),
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
        json=_json_payload("kbase_search_empty"),
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json=_json_payload("kbase_workspace_name_result"),
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
        json=_json_payload("kbase_search_empty"),
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json=_json_payload("kbase_workspace_not_found"),
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json=_json_payload("kbase_workspace_metadata_result"),
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
        headers={
            "Location": f"https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={accession}"
        },
    )
    responses.add(
        responses.GET,
        f"{PROXI_DATASETS_API}/{accession}",
        json=_json_payload("massive_dataset_detail"),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "MassIVE dataset description text."
    assert result.context_type == "description"
    assert result.source == "massive"
    assert result.provider == "massive"
    assert result.attempts == ["massive"]


@responses.activate
def test_massive_link_only_falls_back_and_preserves_publication_metadata() -> None:
    """MassIVE publication links should persist when DataCite supplies the text fallback."""
    doi = "10.25345/C5FX7482Q"
    accession = "MSV000093271"
    publication_doi = "10.1000/massive-paper"
    publication_url = "https://example.org/massive-paper.pdf"
    responses.add(
        responses.GET,
        f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
        status=302,
        headers={
            "Location": f"https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={accession}"
        },
    )
    responses.add(
        responses.GET,
        f"{PROXI_DATASETS_API}/{accession}",
        json={
            "publications": [
                {
                    "url": publication_url,
                    "relatedIdentifier": publication_doi,
                    "relation": "isPublishedIn",
                    "resource_type": "journal-article",
                }
            ]
        },
    )
    for _ in range(3):
        responses.add(
            responses.GET,
            PROXI_DATASETS_API,
            json=_json_payload("proxi_empty_datasets"),
        )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_datacite_abstract_payload(
            "DataCite fallback after MassIVE link-only metadata"),
    )

    result = get_doi_description_or_abstract(doi)

    assert result.context == "DataCite fallback after MassIVE link-only metadata"
    assert result.source == "datacite"
    assert result.attempts == ["massive", "datacite"]
    assert result.publication_urls == [publication_url]
    assert result.publication_dois == [publication_doi]


@responses.activate
def test_massive_proxi_miss_falls_back_to_datacite() -> None:
    """If MassIVE resolver cannot retrieve dataset detail, continue waterfall."""
    doi = "10.25345/C5FX7482Q"
    accession = "MSV000093271"
    responses.add(
        responses.GET,
        f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
        status=302,
        headers={
            "Location": f"https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={accession}"
        },
    )
    responses.add(
        responses.GET,
        f"{PROXI_DATASETS_API}/{accession}",
        json=_json_payload("proxi_error_not_reserved"),
    )
    responses.add(
        responses.GET,
        f"{PROXI_DATASETS_API}",
        json=_json_payload("proxi_empty_datasets"),
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_massive_fallback"),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "DataCite fallback for MassIVE DOI"
    assert result.context_type == "abstract"
    assert result.source == "datacite"
    assert result.provider == "massive"
    assert result.attempts == ["massive", "datacite"]


@responses.activate
def test_massive_uses_datacite_title_context_when_proxi_fails() -> None:
    """Use DataCite subtitle/title fallback and report DataCite as source."""
    doi = "10.25345/c5kk94s01"
    accession = "MSV000100938"
    responses.add(
        responses.GET,
        f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
        status=302,
        headers={
            "Location": f"https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={accession}"
        },
    )
    responses.add(
        responses.GET,
        f"{PROXI_DATASETS_API}/{accession}",
        json=_json_payload("proxi_error_not_reserved"),
    )
    responses.add(
        responses.GET,
        PROXI_DATASETS_API,
        json=_json_payload("proxi_empty_datasets"),
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_massive_title_subtitle"),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Detailed MassIVE subtitle context from DataCite."
    assert result.context_type == "description"
    assert result.source == "datacite"
    assert result.provider == "massive"
    assert result.attempts == ["massive", "datacite"]


@responses.activate
def test_cyverse_provider_api_abstract_wins() -> None:
    """Resolve CyVerse DOI via Terrain metadata and return abstract text."""
    doi = "10.17504/cyverse.dataset.12345"
    target_id = "7cfd91a9-2e07-4224-9c5b-26a04ce24122"
    responses.add(
        responses.POST,
        CYVERSE_METADATA_SEARCH_API,
        json=_json_payload("fixture_cyverse_search_hit",
                           doi=doi, target_id=target_id),
    )
    responses.add(
        responses.GET,
        CYVERSE_METADATA_API,
        json=_json_payload("cyverse_provider_metadata_hit",
                           target_id=target_id),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "CyVerse dataset abstract text."
    assert result.context_type == "abstract"
    assert result.source == "cyverse"
    assert result.provider == "cyverse"
    assert result.attempts == ["cyverse"]


@responses.activate
def test_cyverse_link_only_falls_back_and_preserves_publication_metadata() -> None:
    """CyVerse AVU publication links should survive a later DataCite text fallback."""
    doi = "10.17504/cyverse.dataset.67890"
    target_id = "7cfd91a9-2e07-4224-9c5b-26a04ce24122"
    publication_doi = "10.1000/cyverse-paper"
    publication_url = "https://example.org/cyverse-paper.pdf"
    responses.add(
        responses.POST,
        CYVERSE_METADATA_SEARCH_API,
        json={
            "avus": [
                {"attr": "dc.identifier.doi", "value": doi, "target_id": target_id},
                {"attr": "publication_pdf", "value": publication_url,
                    "target_id": target_id},
                {"attr": "publication_doi", "value": publication_doi,
                    "target_id": target_id},
            ]
        },
    )
    responses.add(
        responses.GET,
        CYVERSE_METADATA_API,
        json={"avus": []},
    )
    responses.add(
        responses.GET,
        f"{DOI_RESOLVER_URL}/{doi}",
        status=404,
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_datacite_abstract_payload(
            "DataCite fallback after CyVerse link-only metadata"),
    )

    result = get_doi_description_or_abstract(doi)

    assert result.context == "DataCite fallback after CyVerse link-only metadata"
    assert result.source == "datacite"
    assert result.attempts == ["cyverse", "datacite"]
    assert result.publication_urls == [publication_url]
    assert result.publication_dois == [publication_doi]


@responses.activate
def test_cyverse_datacommons_metadata_fallback_returns_description(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If Terrain metadata misses, resolve CyVerse context from Data Commons metadata."""
    doi = "10.25739/hr33-4321"
    datacommons_path = "/iplant/home/shared/commons_repo/curated/Reicher_PooledProteinTagging_2020"
    item_id = "a4c1bfac-f74e-11ea-92e8-90e2ba675364"
    with caplog.at_level("INFO"):
        responses.add(
            responses.POST,
            CYVERSE_METADATA_SEARCH_API,
            json=_json_payload("cyverse_search_empty"),
        )
        responses.add(
            responses.GET,
            f"{DOI_RESOLVER_URL}/{doi}",
            status=302,
            headers={
                "Location": f"https://datacommons.cyverse.org/browse{datacommons_path}"},
        )
        responses.add(
            responses.GET,
            CYVERSE_DATACOMMONS_API,
            match=[
                responses.matchers.query_param_matcher(
                    {
                        "djng_url_name": "api_stat",
                        "djng_url_kwarg_path": datacommons_path,
                    }
                )
            ],
            json={"id": item_id},
        )
        responses.add(
            responses.GET,
            CYVERSE_DATACOMMONS_API,
            match=[
                responses.matchers.query_param_matcher(
                    {
                        "djng_url_name": "api_metadata",
                        "djng_url_kwarg_item_id": item_id,
                    }
                )
            ],
            json={
                "metadata": {
                    "Identifier": {"attr": "identifier", "value": doi, "label": "Identifier"},
                    "Description": {
                        "attr": "description",
                        "value": "CyVerse Data Commons description text.",
                        "label": "Description",
                    },
                }
            },
        )

        result = get_doi_description_or_abstract(doi, sources=["cyverse"])
    assert result.context == "CyVerse Data Commons description text."
    assert result.context_type == "description"
    assert result.source == "cyverse"
    assert result.attempts == ["cyverse"]
    assert result.error is None
    assert result.source_errors == {}
    assert (
        "CyVerse resolved DOI 10.25739/hr33-4321 through Data Commons after Terrain miss"
        in caplog.text
    )


@responses.activate
def test_cyverse_terrain_unusable_metadata_falls_back_to_datacommons() -> None:
    """If Terrain finds the DOI but yields no usable context, fall back to Data Commons."""
    doi = "10.25739/zh2v-4p15"
    target_id = "df05b5b2-9896-11eb-93b0-90e2ba675364"
    datacommons_path = (
        "/iplant/home/shared/commons_repo/curated/"
        "Carolyn_Lawrence_Dill_GOMAP_Solanum_lycopersicum_ITAG4.1.v1_April_2021.r1"
    )
    item_id = target_id

    responses.add(
        responses.POST,
        CYVERSE_METADATA_SEARCH_API,
        json=_json_payload("fixture_cyverse_search_hit",
                           doi=doi, target_id=target_id),
    )
    responses.add(
        responses.GET,
        CYVERSE_METADATA_API,
        json={
            "avus": [
                {
                    "attr": "dc.identifier.doi",
                    "value": doi,
                    "target_id": target_id,
                },
                {
                    "attr": "creator",
                    "value": "Carolyn Lawrence-Dill",
                    "target_id": target_id,
                },
            ]
        },
    )
    responses.add(
        responses.GET,
        f"{DOI_RESOLVER_URL}/{doi}",
        status=302,
        headers={
            "Location": f"https://datacommons.cyverse.org/browse{datacommons_path}"},
    )
    responses.add(
        responses.GET,
        CYVERSE_DATACOMMONS_API,
        match=[
            responses.matchers.query_param_matcher(
                {
                    "djng_url_name": "api_stat",
                    "djng_url_kwarg_path": datacommons_path,
                }
            )
        ],
        json={"id": item_id},
    )
    responses.add(
        responses.GET,
        CYVERSE_DATACOMMONS_API,
        match=[
            responses.matchers.query_param_matcher(
                {
                    "djng_url_name": "api_metadata",
                    "djng_url_kwarg_item_id": item_id,
                }
            )
        ],
        json={
            "metadata": {
                "Identifier": {"attr": "identifier", "value": doi, "label": "Identifier"},
                "Description": {
                    "attr": "description",
                    "value": "CyVerse Data Commons fallback description.",
                    "label": "Description",
                },
            }
        },
    )

    result = get_doi_description_or_abstract(doi, sources=["cyverse"])

    assert result.context == "CyVerse Data Commons fallback description."
    assert result.context_type == "description"
    assert result.source == "cyverse"
    assert result.attempts == ["cyverse"]
    assert result.error is None
    assert result.source_errors == {}
    assert len(responses.calls) == 5
    terrain_url_raw = responses.calls[1].request.url
    assert terrain_url_raw is not None
    terrain_url_str: str = (
        terrain_url_raw.decode() if isinstance(
            terrain_url_raw, bytes) else terrain_url_raw
    )
    terrain_parsed = urlparse(terrain_url_str)
    assert f"{terrain_parsed.scheme}://{terrain_parsed.netloc}{terrain_parsed.path}" == (
        CYVERSE_METADATA_API
    )
    terrain_query: str = terrain_parsed.query
    assert parse_qs(terrain_query) == {"target-id": [target_id]}
    doi_request_url_raw = responses.calls[2].request.url
    assert doi_request_url_raw is not None
    doi_request_url: str = (
        doi_request_url_raw.decode()
        if isinstance(doi_request_url_raw, bytes)
        else doi_request_url_raw
    )
    assert doi_request_url == f"{DOI_RESOLVER_URL}/{doi}"


@responses.activate
def test_cyverse_miss_falls_back_to_datacite() -> None:
    """If CyVerse API has no matching metadata, continue waterfall to DataCite."""
    doi = "10.17504/cyverse.dataset.67890"
    responses.add(
        responses.POST,
        CYVERSE_METADATA_SEARCH_API,
        json=_json_payload("cyverse_search_empty"),
    )
    responses.add(
        responses.GET,
        f"{DOI_RESOLVER_URL}/{doi}",
        status=404,
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_cyverse_fallback"),
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
    doi = "10.17504/cyverse.dataset.67890"
    responses.add(
        responses.POST,
        CYVERSE_METADATA_SEARCH_API,
        json=_json_payload("cyverse_search_empty"),
    )
    responses.add(
        responses.GET,
        f"{DOI_RESOLVER_URL}/{doi}",
        status=404,
    )

    result = get_doi_description_or_abstract(doi, sources=["cyverse"])
    assert result.context is None
    assert result.source is None
    assert (
        result.error
        == "No description or abstract found in any source. Check source_errors for details."
    )
    assert result.attempts == ["cyverse"]


@responses.activate
def test_zenodo_provider_api_is_used_first() -> None:
    """For Zenodo-prefix DOIs, query Zenodo API before generic fallbacks."""
    doi = "10.5281/zenodo.7406532"
    responses.add(
        responses.GET,
        ZENODO_API,
        json=_json_payload("zenodo_description_html_hit"),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Zenodo dataset description."
    assert result.context_type == "description"
    assert result.source == "zenodo"
    assert result.provider == "zenodo"
    assert result.attempts == ["zenodo"]


@responses.activate
def test_zenodo_link_only_falls_back_and_preserves_publication_metadata() -> None:
    """Zenodo record file links should be preserved when description comes from DataCite."""
    doi = "10.5281/zenodo.7406532"
    publication_doi = "10.1000/zenodo-paper"
    publication_url = "https://zenodo.org/api/records/7406533/files/paper.pdf/content"
    responses.add(
        responses.GET,
        ZENODO_API,
        json={
            "hits": {
                "hits": [
                    {
                        "metadata": {
                            "related_identifiers": [
                                {
                                    "identifier": publication_doi,
                                    "relation": "isPublishedIn",
                                    "resource_type": "publication-preprint",
                                }
                            ]
                        },
                        "files": [
                            {
                                "key": "paper.pdf",
                                "links": {"self": publication_url},
                            }
                        ],
                    }
                ]
            }
        },
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_datacite_abstract_payload(
            "DataCite fallback after Zenodo link-only metadata"),
    )

    result = get_doi_description_or_abstract(doi)

    assert result.context == "DataCite fallback after Zenodo link-only metadata"
    assert result.source == "datacite"
    assert result.attempts == ["zenodo", "datacite"]
    assert result.publication_urls == [publication_url]
    assert result.publication_dois == [publication_doi]


@responses.activate
def test_zenodo_concept_doi_query_prefers_zenodo_over_datacite() -> None:
    """Resolve Zenodo concept DOI using conceptdoi-aware query instead of fallback."""
    doi = "10.5281/zenodo.7406532"

    def zenodo_callback(request: PreparedRequest) -> tuple[int, dict[str, str], str]:
        parsed_qs = parse_qs(urlparse(request.url or "").query)
        query = parsed_qs.get("q", [""])[0]
        if 'conceptdoi:"10.5281/zenodo.7406532"' in query:
            body = _json_payload("zenodo_concept_hit")
            return 200, {"Content-Type": "application/json"}, json.dumps(body)
        return (
            200,
            {"Content-Type": "application/json"},
            json.dumps(_json_payload("zenodo_empty_hits")),
        )

    responses.add_callback(
        responses.GET,
        ZENODO_API,
        callback=zenodo_callback,
        content_type="application/json",
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_zenodo_unused_fallback"),
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
        json=_json_payload("zenodo_mixed_hits"),
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
    responses.add(responses.GET, ZENODO_API,
                  json=_json_payload("zenodo_empty_hits"))
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_zenodo_description_fallback"),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Datacite description fallback"
    assert result.context_type == "description"
    assert result.source == "datacite"
    assert result.attempts == ["zenodo", "datacite"]


@responses.activate
def test_source_errors_include_upstream_http_codes() -> None:
    """Capture per-source HTTP status details for pipeline retry/alert handling."""
    doi = "10.5281/zenodo.7406532"
    _add_retriable_http_response(
        responses.GET,
        ZENODO_API,
        status=503,
        json_payload=_json_payload("service_unavailable_detail"),
    )
    _add_retriable_http_response(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        status=502,
        json_payload={},
    )
    _add_retriable_http_response(
        responses.GET,
        f"{CROSSREF_API}/{doi}",
        status=429,
        json_payload={},
    )
    _add_retriable_http_response(
        responses.GET,
        f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
        status=503,
        json_payload={},
    )
    _mock_openalex_miss(doi)
    _mock_pubmed_miss()

    result = get_doi_description_or_abstract(doi)
    assert result.context is None
    assert (
        result.error
        == "No description or abstract found in any source. Check source_errors for details."
    )

    assert result.attempts == [
        "zenodo",
        "datacite",
        "crossref",
        "content_negotiation",
        "openalex",
        "pubmed",
    ]
    assert "HTTP 503" in result.source_errors["zenodo"]
    assert "HTTP 502" in result.source_errors["datacite"]
    assert "HTTP 429" in result.source_errors["crossref"]
    assert "HTTP 503" in result.source_errors["content_negotiation"]
    assert (
        "OpenAlex response contained no abstract_inverted_index" in result.source_errors[
            "openalex"]
    )
    assert "PubMed ID converter found no PMID for DOI" in result.source_errors["pubmed"]


@responses.activate
def test_source_errors_preserved_on_successful_fallback() -> None:
    """Keep upstream source failure detail even when a later source succeeds."""
    doi = "10.5281/zenodo.7406532"
    _add_retriable_http_response(
        responses.GET,
        ZENODO_API,
        status=503,
        json_payload=_json_payload("service_unavailable_detail"),
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_zenodo_description_fallback"),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "Datacite description fallback"
    assert result.source == "datacite"
    assert result.attempts == ["zenodo", "datacite"]
    assert "HTTP 503" in result.source_errors["zenodo"]


@responses.activate
def test_ess_dive_api_abstract_wins() -> None:
    """Return ESS-DIVE abstract immediately when provider API resolves it."""
    doi = "10.15485/1729719"
    responses.add(
        responses.GET,
        ESS_DIVE_API,
        json=_json_payload("ess_dive_api_abstract"),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "ESS-DIVE abstract text"
    assert result.context_type == "abstract"
    assert result.source == "ess-dive"
    assert result.provider == "ess-dive"
    assert result.attempts == ["ess-dive"]


@responses.activate
def test_ess_dive_link_only_falls_back_and_preserves_publication_metadata() -> None:
    """ESS-DIVE linked publication metadata should survive a DataCite text fallback."""
    doi = "10.15485/1729719"
    publication_doi = "10.1000/ess-dive-paper"
    publication_url = "https://example.org/ess-dive-paper.pdf"
    responses.add(
        responses.GET,
        ESS_DIVE_API,
        json={
            "data": [
                {
                    "files": [
                        {
                            "download_url": publication_url,
                            "mimetype": "application/pdf",
                        }
                    ],
                    "relatedIdentifiers": [
                        {
                            "relatedIdentifier": publication_doi,
                            "relatedIdentifierType": "DOI",
                            "relationType": "IsPublishedIn",
                        }
                    ],
                }
            ]
        },
    )
    for _ in range(2):
        responses.add(
            responses.GET,
            DATAONE_CN_SOLR_API,
            body=XML_PAYLOAD_FIXTURES["dataone_empty_result_xml"],
            content_type="application/xml",
        )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_datacite_abstract_payload(
            "DataCite fallback after ESS-DIVE link-only metadata"),
    )

    result = get_doi_description_or_abstract(doi)

    assert result.context == "DataCite fallback after ESS-DIVE link-only metadata"
    assert result.source == "datacite"
    assert result.attempts == ["ess-dive", "datacite"]
    assert result.publication_urls == [publication_url]
    assert result.publication_dois == [publication_doi]


@responses.activate
def test_ess_dive_api_401_uses_dataone_fallback() -> None:
    """Use DataONE ESS_DIVE index when ESS-DIVE packages API is unauthorized."""
    doi = "10.15485/1729719"
    responses.add(
        responses.GET,
        ESS_DIVE_API,
        status=401,
        json=_json_payload("unauthorized_detail"),
    )
    responses.add(
        responses.GET,
        DATAONE_CN_SOLR_API,
        body=XML_PAYLOAD_FIXTURES["dataone_ess_dive_hit_xml"],
        content_type="application/xml",
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "ESS-DIVE DataONE abstract text."
    assert result.context_type == "abstract"
    assert result.source == "ess-dive"
    assert result.provider == "ess-dive"
    assert result.attempts == ["ess-dive"]


@responses.activate
def test_ess_dive_dataone_rejects_doctype_xml_and_falls_back_to_datacite() -> None:
    """Reject DataONE XML with DTD/entity declarations and continue waterfall."""
    doi = "10.15485/1729719"
    responses.add(
        responses.GET,
        ESS_DIVE_API,
        status=401,
        json=_json_payload("unauthorized_detail"),
    )

    for _ in range(3):
        responses.add(
            responses.GET,
            DATAONE_CN_SOLR_API,
            body=XML_PAYLOAD_FIXTURES["dataone_ess_dive_unsafe_xml"],
            content_type="application/xml",
        )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_ess_dive_after_unsafe_xml"),
    )

    result = get_doi_description_or_abstract(doi)
    assert result.context == "DataCite fallback after unsafe DataONE XML"
    assert result.context_type == "abstract"
    assert result.source == "datacite"
    assert result.provider == "ess-dive"
    assert result.attempts == ["ess-dive", "datacite"]


@responses.activate
def test_ess_dive_dataone_query_tries_uppercase_variant_for_wtr_dois() -> None:
    """Retry DataONE query with uppercase DOI variant for WTR-style series IDs."""
    doi = "10.21952/wtr/1573029"
    responses.add(
        responses.GET,
        ESS_DIVE_API,
        status=401,
        json=_json_payload("unauthorized_detail"),
    )

    def dataone_callback(request: PreparedRequest) -> tuple[int, dict[str, str], str]:
        query = parse_qs(urlparse(request.url or "").query).get("q", [""])[0]
        if "10.21952/wtr/1573029" in query:
            return (
                200,
                {"Content-Type": "application/xml"},
                XML_PAYLOAD_FIXTURES["dataone_empty_result_xml"],
            )
        if "10.21952/WTR/1573029" in query:
            return (
                200,
                {"Content-Type": "application/xml"},
                XML_PAYLOAD_FIXTURES["dataone_wtr_hit_xml"],
            )
        return (
            200,
            {"Content-Type": "application/xml"},
            XML_PAYLOAD_FIXTURES["dataone_empty_result_xml"],
        )

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
    responses.add(
        responses.GET,
        ESS_DIVE_API,
        status=401,
        json=_json_payload("unauthorized_detail"),
    )
    responses.add(
        responses.GET,
        DATAONE_CN_SOLR_API,
        body=XML_PAYLOAD_FIXTURES["dataone_empty_result_xml"],
        content_type="application/xml",
    )
    responses.add(
        responses.GET,
        DATAONE_CN_SOLR_API,
        body=XML_PAYLOAD_FIXTURES["dataone_empty_result_xml"],
        content_type="application/xml",
    )
    responses.add(
        responses.GET,
        DATAONE_CN_SOLR_API,
        body=XML_PAYLOAD_FIXTURES["dataone_empty_result_xml"],
        content_type="application/xml",
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_ess_dive_fallback"),
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
        json=_json_payload("jgi_empty_response"),
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_empty_descriptions"),
    )
    responses.add(
        responses.GET, f"{CROSSREF_API}/{doi}", json=_json_payload("crossref_empty_message")
    )
    responses.add(
        responses.GET,
        f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
        json=_json_payload("empty_object"),
    )
    _mock_openalex_miss(doi)
    _mock_pubmed_miss()

    result = get_doi_description_or_abstract(doi)
    assert result.context is None
    assert result.provider == "jgi"
    assert (
        result.error
        == "No description or abstract found in any source. Check source_errors for details."
    )
    assert result.attempts == [
        "jgi",
        "datacite",
        "crossref",
        "content_negotiation",
        "openalex",
        "pubmed",
    ]


@responses.activate
def test_all_sources_miss_returns_clean_error() -> None:
    """Return a clean no-context error with full attempted-source trace."""
    doi = "10.25982/109073.30/1895615"
    responses.add(
        responses.POST,
        KBASE_SEARCH_API,
        json=_json_payload("kbase_search_empty"),
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json=_json_payload("kbase_workspace_not_found"),
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json=_json_payload("kbase_workspace_not_found"),
    )
    responses.add(
        responses.POST,
        KBASE_WORKSPACE_API,
        json=_json_payload("kbase_workspace_not_found"),
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json=_json_payload("datacite_empty_descriptions"),
    )
    responses.add(
        responses.GET, f"{CROSSREF_API}/{doi}", json=_json_payload("crossref_empty_message")
    )
    responses.add(
        responses.GET,
        f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
        json=_json_payload("empty_object"),
    )
    _mock_openalex_miss(doi)
    _mock_pubmed_miss()

    result = get_doi_description_or_abstract(doi)
    assert result.context is None
    assert (
        result.error
        == "No description or abstract found in any source. Check source_errors for details."
    )
    assert result.provider == "kbase"
    assert result.attempts == [
        "kbase",
        "datacite",
        "crossref",
        "content_negotiation",
        "openalex",
        "pubmed",
    ]


# ---------------------------------------------------------------------------
# DOI matching helpers
# ---------------------------------------------------------------------------


def test_text_mentions_doi_matches_exact_variants() -> None:
    """Exact DOI references should match across bare, doi:, and URL forms."""
    requested = "10.1038/s41564-020-00861-0"
    text = (
        "References: doi:10.1038/s41564-020-00861-0 and "
        "https://doi.org/10.1038/s41564-020-00861-0."
    )
    assert text_mentions_doi(text, requested) is True


def test_text_mentions_doi_handles_trailing_parenthesis() -> None:
    """Trailing punctuation after DOI references should not prevent matching."""
    requested = "10.1016/0038-0717(85)90144-0"
    text = "Methods (https://doi.org/10.1016/0038-0717(85)90144-0)."
    assert text_mentions_doi(text, requested) is True


def test_text_mentions_doi_rejects_longer_suffix_match() -> None:
    """Prefix-only matches must not count as DOI matches."""
    assert text_mentions_doi("See 10.1234/abcd", "10.1234/abc") is False
    assert text_mentions_doi("See 10.1234/abc123", "10.1234/abc") is False


def test_text_mentions_doi_rejects_dot_extended_suffix_match() -> None:
    """Token continuation via punctuation should not match a shorter DOI."""
    assert text_mentions_doi("See 10.1234/abc.def", "10.1234/abc") is False


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
        result = get_doi_description_or_abstract(doi, skip_classification=True)
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
        result = get_doi_description_or_abstract(doi, skip_classification=True)
        assert result.attempts[0] == source

    assert result.context is None
    assert (
        result.error
        == "No description or abstract found in any source. Check source_errors for details."
    )


@integration
@pytest.mark.parametrize("doi", _live_dois_for_source("cyverse"))
def test_live_cyverse_explicit_source_route_returns_context(doi: str) -> None:
    """Explicit CyVerse source lookups should resolve context for each curated live DOI."""
    result = get_doi_description_or_abstract(doi, sources=["cyverse"])

    assert result.context is not None, f"Expected CyVerse context for {doi}; got {result.error!r}"
    assert result.source == "cyverse"
    assert result.attempts == ["cyverse"]
    assert result.context_type in {"abstract", "description"}
    assert result.error is None
    assert result.source_errors == {}


@integration
def test_try_edi_live_returns_context_for_curated_doi() -> None:
    """Direct EDI resolver should return context for at least one curated live DOI."""
    edi_dois = _live_dois_for_source("edi")
    if not edi_dois:
        pytest.skip("No curated EDI live DOIs configured")

    failures: list[str] = []
    for doi in edi_dois:
        errors: list[str] = []
        context = try_edi(doi, errors=errors)
        if context is None:
            failures.append(
                f"{doi}: {'; '.join(errors) if errors else 'no context'}")
            continue

        assert context.kind in {"abstract", "description"}
        assert context.text
        assert context.raw_text
        return

    pytest.fail(
        "EDI resolver returned no context for all curated live DOIs: " +
        " | ".join(failures)
    )


@integration
def test_live_edi_explicit_source_route_returns_provider_context() -> None:
    """Explicit EDI source lookup should resolve via EDI with provider-aware attempts."""
    edi_dois = _live_dois_for_source("edi")
    if not edi_dois:
        pytest.skip("No curated EDI live DOIs configured")

    failures: list[str] = []
    for doi in edi_dois:
        result = get_doi_description_or_abstract(doi, sources=["edi"])
        if result.context is None:
            failures.append(f"{doi}: {result.error or 'no context'}")
            continue

        assert result.source == "edi"
        assert result.provider == "edi"
        assert result.attempts == ["edi"]
        assert result.context_type in {"abstract", "description"}
        return

    pytest.fail(
        "Explicit EDI source lookups returned no context for all curated live DOIs: "
        + " | ".join(failures)
    )


@integration
def test_try_emsl_live_returns_context_for_curated_doi() -> None:
    """Direct EMSL resolver should return context for at least one curated live DOI."""
    emsl_dois = _live_dois_for_source("emsl")
    if not emsl_dois:
        pytest.skip("No curated EMSL live DOIs configured")

    failures: list[str] = []
    for doi in emsl_dois:
        errors: list[str] = []
        context = try_emsl(doi, errors=errors)
        if context is None:
            failures.append(
                f"{doi}: {'; '.join(errors) if errors else 'no context'}")
            continue

        assert context.kind in {"abstract", "description"}
        assert context.text
        assert context.raw_text
        return

    pytest.fail(
        "EMSL resolver returned no context for all curated live DOIs: " +
        " | ".join(failures)
    )


@integration
def test_live_emsl_explicit_source_route_returns_provider_context() -> None:
    """Explicit EMSL source lookup should resolve via EMSL with provider-aware attempts."""
    emsl_dois = _live_dois_for_source("emsl")
    if not emsl_dois:
        pytest.skip("No curated EMSL live DOIs configured")

    failures: list[str] = []
    for doi in emsl_dois:
        result = get_doi_description_or_abstract(doi, sources=["emsl"])
        if result.context is None:
            failures.append(f"{doi}: {result.error or 'no context'}")
            continue

        assert result.source == "emsl"
        assert result.provider == "emsl"
        assert result.attempts == ["emsl"]
        assert result.context_type in {"abstract", "description"}
        return

    pytest.fail(
        "Explicit EMSL source lookups returned no context for all curated live DOIs: "
        + " | ".join(failures)
    )


@integration
def test_try_jgi_live_returns_context_for_curated_doi() -> None:
    """Direct JGI resolver should return context for at least one curated live DOI."""
    jgi_dois = _live_dois_for_source("jgi")
    if not jgi_dois:
        pytest.skip("No curated JGI live DOIs configured")

    failures: list[str] = []
    for doi in jgi_dois:
        errors: list[str] = []
        context = try_jgi(doi, errors=errors)
        if context is None:
            failures.append(
                f"{doi}: {'; '.join(errors) if errors else 'no context'}")
            continue

        assert context.kind in {"abstract", "description"}
        assert context.text
        assert context.raw_text
        return

    pytest.fail(
        "JGI resolver returned no context for all curated live DOIs: " +
        " | ".join(failures)
    )


@integration
def test_live_jgi_explicit_source_route_returns_provider_context() -> None:
    """Explicit JGI source lookup should resolve via JGI with provider-aware attempts."""
    jgi_dois = _live_dois_for_source("jgi")
    if not jgi_dois:
        pytest.skip("No curated JGI live DOIs configured")

    failures: list[str] = []
    for doi in jgi_dois:
        result = get_doi_description_or_abstract(doi, sources=["jgi"])
        if result.context is None:
            failures.append(f"{doi}: {result.error or 'no context'}")
            continue

        assert result.source == "jgi"
        assert result.provider == "jgi"
        assert result.attempts == ["jgi"]
        assert result.context_type in {"abstract", "description"}
        return

    pytest.fail(
        "Explicit JGI source lookups returned no context for all curated live DOIs: "
        + " | ".join(failures)
    )


@integration
def test_try_kbase_live_returns_context_for_curated_doi() -> None:
    """Direct KBase resolver should return context for at least one curated live DOI."""
    kbase_dois = _live_dois_for_source("kbase")
    if not kbase_dois:
        pytest.skip("No curated KBase live DOIs configured")

    failures: list[str] = []
    for doi in kbase_dois:
        errors: list[str] = []
        context = try_kbase(doi, errors=errors)
        if context is None:
            failures.append(
                f"{doi}: {'; '.join(errors) if errors else 'no context'}")
            continue

        assert context.kind in {"abstract", "description"}
        assert context.text
        assert context.raw_text
        return

    pytest.fail(
        "KBase resolver returned no context for all curated live DOIs: " +
        " | ".join(failures)
    )


@integration
def test_live_kbase_explicit_source_route_returns_provider_context() -> None:
    """Explicit KBase source lookup should resolve via KBase with provider-aware attempts."""
    kbase_dois = _live_dois_for_source("kbase")
    if not kbase_dois:
        pytest.skip("No curated KBase live DOIs configured")

    failures: list[str] = []
    for doi in kbase_dois:
        result = get_doi_description_or_abstract(doi, sources=["kbase"])
        if result.context is None:
            failures.append(f"{doi}: {result.error or 'no context'}")
            continue

        assert result.source == "kbase"
        assert result.provider == "kbase"
        assert result.attempts == ["kbase"]
        assert result.context_type in {"abstract", "description"}
        return

    pytest.fail(
        "Explicit KBase source lookups returned no context for all curated live DOIs: "
        + " | ".join(failures)
    )
