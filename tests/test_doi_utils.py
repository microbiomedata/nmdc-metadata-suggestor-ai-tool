"""Tests for DOI validation, normalization, and classification.

Uses the curated test fixture at tests/fixtures/doi_test_cases.json.
Unit tests mock HTTP with ``responses``; integration tests hit real APIs.
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import responses
from requests.exceptions import ConnectionError as RequestsConnectionError

import nmdc_metadata_suggestor.publication_ingestion.doi_utils as doi_utils
from nmdc_metadata_suggestor.publication_ingestion.doi_utils import (
    CROSSREF_API,
    DATACITE_API,
    DOI_HANDLE_API,
    DOI_RA_API,
    classify_doi,
    detect_registration_agency,
    infer_nmdc_category,
    normalize_doi,
    request_with_retry,
    validate_doi,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "doi_test_cases.json"

integration = pytest.mark.integration


@pytest.fixture()
def test_cases() -> list[dict[str, object]]:
    """Load the curated DOI test cases."""
    with open(FIXTURE_PATH) as f:
        return json.load(f)["test_cases"]


# ---------------------------------------------------------------------------
# normalize_doi — pure function, no mocking
# ---------------------------------------------------------------------------

NORMALIZE_CASES = [
    ("10.1038/s41564-020-00861-0", "10.1038/s41564-020-00861-0"),
    ("https://doi.org/10.1038/s41564-020-00861-0", "10.1038/s41564-020-00861-0"),
    ("http://doi.org/10.1038/s41564-020-00861-0", "10.1038/s41564-020-00861-0"),
    ("https://dx.doi.org/10.1038/s41564-020-00861-0", "10.1038/s41564-020-00861-0"),
    ("doi:10.1038/s41564-020-00861-0", "10.1038/s41564-020-00861-0"),
    ("DOI:10.1038/s41564-020-00861-0", "10.1038/s41564-020-00861-0"),
    ("HTTPS://DOI.ORG/10.1038/s41564-020-00861-0", "10.1038/s41564-020-00861-0"),
    ("<10.1038/s41564-020-00861-0>", "10.1038/s41564-020-00861-0"),
    ("10.1038/s41564-020-00861-0.", "10.1038/s41564-020-00861-0"),
    ("10.1038/s41564-020-00861-0,", "10.1038/s41564-020-00861-0"),
    ("  10.1038/s41564-020-00861-0  ", "10.1038/s41564-020-00861-0"),
    ("10.1016/0038-0717(85)90144-0", "10.1016/0038-0717(85)90144-0"),
]


@pytest.mark.parametrize("raw,expected", NORMALIZE_CASES, ids=[c[0][:40] for c in NORMALIZE_CASES])
def test_normalize_doi(raw: str, expected: str) -> None:
    assert normalize_doi(raw) == expected


# ---------------------------------------------------------------------------
# validate_doi — mocked Handle API
# ---------------------------------------------------------------------------


class TestValidateDoi:
    @responses.activate
    def test_valid(self) -> None:
        doi = "10.1038/s41564-020-00861-0"
        responses.add(responses.GET, f"{DOI_HANDLE_API}/{doi}", json={"responseCode": 1})
        result = validate_doi(doi)
        assert result.is_valid is True
        assert result.handle_response_code == 1

    @responses.activate
    def test_not_found(self) -> None:
        doi = "10.99999/fake-doi-does-not-exist"
        responses.add(responses.GET, f"{DOI_HANDLE_API}/{doi}", json={"responseCode": 100})
        assert validate_doi(doi).is_valid is False

    def test_malformed_skips_api(self) -> None:
        for bad in ("not-a-doi-at-all", ""):
            result = validate_doi(bad)
            assert result.is_valid is False
            assert result.error == "Malformed DOI syntax"

    @responses.activate
    def test_network_error(self) -> None:
        doi = "10.1038/s41564-020-00861-0"
        responses.add(
            responses.GET, f"{DOI_HANDLE_API}/{doi}", body=RequestsConnectionError("down")
        )
        result = validate_doi(doi)
        assert result.is_valid is False
        assert "Request failed" in (result.error or "")


# ---------------------------------------------------------------------------
# detect_registration_agency — mocked doiRA API
# ---------------------------------------------------------------------------


@responses.activate
@pytest.mark.parametrize(
    "doi,expected_ra",
    [
        ("10.1038/s41564-020-00861-0", "Crossref"),
        ("10.15485/1729719", "DataCite"),
    ],
)
def test_detect_registration_agency(doi: str, expected_ra: str) -> None:
    responses.add(responses.GET, f"{DOI_RA_API}/{doi}", json=[{"DOI": doi, "RA": expected_ra}])
    assert detect_registration_agency(doi) == expected_ra


@responses.activate
def test_detect_ra_network_error() -> None:
    doi = "10.1038/s41564-020-00861-0"
    responses.add(responses.GET, f"{DOI_RA_API}/{doi}", body=RequestsConnectionError("down"))
    assert detect_registration_agency(doi) is None


# ---------------------------------------------------------------------------
# infer_nmdc_category — pure function, no mocking
# ---------------------------------------------------------------------------

INFER_CASES = [
    # (agency, resource_type, resource_type_general, expected)
    ("Crossref", "journal-article", None, "publication_doi"),
    ("Crossref", "posted-content", None, "publication_doi"),
    ("Crossref", "book-chapter", None, "publication_doi"),
    ("Crossref", "dataset", None, "dataset_doi"),
    ("Crossref", "grant", None, "award_doi"),
    ("Crossref", "component", None, None),
    ("DataCite", None, "Dataset", "dataset_doi"),
    ("DataCite", None, "JournalArticle", "publication_doi"),
    ("DataCite", None, "Award", "award_doi"),
    ("DataCite", None, "OutputManagementPlan", "data_management_plan_doi"),
    ("DataCite", None, "Software", None),
    (None, "journal-article", None, None),
    ("Crossref", None, None, None),
]


@pytest.mark.parametrize(
    "agency,rtype,rtype_general,expected",
    INFER_CASES,
    ids=[f"{c[0]}-{c[1] or c[2] or 'none'}" for c in INFER_CASES],
)
def test_infer_nmdc_category(
    agency: str | None, rtype: str | None, rtype_general: str | None, expected: str | None
) -> None:
    assert infer_nmdc_category(agency, rtype, rtype_general) == expected


# ---------------------------------------------------------------------------
# request_with_retry — unit tests for retry/connection hygiene
# ---------------------------------------------------------------------------


def test_request_with_retry_closes_retry_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retryable responses are closed before the next retry attempt."""
    first = Mock()
    first.status_code = 503
    first.headers = {}
    first.close = Mock()

    second = Mock()
    second.status_code = 200
    second.headers = {}
    second.close = Mock()

    request_mock = Mock(side_effect=[first, second])
    monkeypatch.setattr(doi_utils._HTTP_SESSION, "request", request_mock)
    monkeypatch.setattr(doi_utils.time, "sleep", Mock())

    result = request_with_retry("GET", "https://example.org", max_attempts=2, backoff_seconds=0)

    assert result is second
    assert request_mock.call_count == 2
    first.close.assert_called_once_with()
    second.close.assert_not_called()


def test_request_with_retry_closes_exception_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exception-attached responses are closed before retrying."""
    transient_response = Mock()
    transient_response.close = Mock()

    transient_exc = RequestsConnectionError("temporary failure")
    transient_exc.response = transient_response

    success = Mock()
    success.status_code = 200
    success.headers = {}
    success.close = Mock()

    request_mock = Mock(side_effect=[transient_exc, success])
    monkeypatch.setattr(doi_utils._HTTP_SESSION, "request", request_mock)
    monkeypatch.setattr(doi_utils.time, "sleep", Mock())

    result = request_with_retry("GET", "https://example.org", max_attempts=2, backoff_seconds=0)

    assert result is success
    transient_response.close.assert_called_once_with()


# ---------------------------------------------------------------------------
# classify_doi — mocked RA + agency APIs
# ---------------------------------------------------------------------------


def _mock_crossref(doi: str, resource_type: str, publisher: str = "Test") -> None:
    """Register mocked RA + Crossref responses."""
    responses.add(responses.GET, f"{DOI_RA_API}/{doi}", json=[{"DOI": doi, "RA": "Crossref"}])
    responses.add(
        responses.GET,
        f"{CROSSREF_API}/{doi}",
        json={"message": {"type": resource_type, "publisher": publisher}},
    )


def _mock_datacite(
    doi: str,
    resource_type: str,
    resource_type_general: str,
    publisher: str = "Test",
) -> None:
    """Register mocked RA + DataCite responses."""
    responses.add(responses.GET, f"{DOI_RA_API}/{doi}", json=[{"DOI": doi, "RA": "DataCite"}])
    responses.add(
        responses.GET,
        f"{DATACITE_API}/{doi}",
        json={
            "data": {
                "attributes": {
                    "types": {
                        "resourceType": resource_type,
                        "resourceTypeGeneral": resource_type_general,
                    },
                    "publisher": publisher,
                }
            }
        },
    )


class TestClassifyDoi:
    @responses.activate
    def test_crossref_publication(self) -> None:
        doi = "10.1038/s41564-020-00861-0"
        _mock_crossref(doi, "journal-article", "Springer Science and Business Media LLC")
        result = classify_doi(doi)
        assert result.is_valid is True
        assert result.registration_agency == "Crossref"
        assert result.resource_type == "journal-article"
        assert result.inferred_nmdc_category == "publication_doi"
        assert result.prefix == "10.1038"

    @responses.activate
    def test_datacite_dataset(self) -> None:
        doi = "10.15485/1729719"
        _mock_datacite(doi, "Dataset", "Dataset", "ESS-DIVE")
        result = classify_doi(doi)
        assert result.is_valid is True
        assert result.registration_agency == "DataCite"
        assert result.inferred_nmdc_category == "dataset_doi"

    @responses.activate
    def test_datacite_award(self) -> None:
        doi = "10.46936/jejc.proj.2014.48483/60005501"
        _mock_datacite(doi, "Project", "Award", "EMSL")
        result = classify_doi(doi)
        assert result.inferred_nmdc_category == "award_doi"

    def test_malformed(self) -> None:
        for bad in ("not-a-doi-at-all", ""):
            result = classify_doi(bad)
            assert result.is_valid is False
            assert result.error == "Malformed DOI syntax"

    @responses.activate
    def test_unknown_ra(self) -> None:
        doi = "10.1234/some-medra-doi"
        responses.add(responses.GET, f"{DOI_RA_API}/{doi}", json=[{"DOI": doi, "RA": "mEDRA"}])
        result = classify_doi(doi)
        assert result.is_valid is True
        assert result.inferred_nmdc_category is None

    @responses.activate
    def test_crossref_api_error_still_valid(self) -> None:
        """RA detected but Crossref API fails — DOI is still valid, just unclassified."""
        doi = "10.1038/s41564-020-00861-0"
        responses.add(responses.GET, f"{DOI_RA_API}/{doi}", json=[{"DOI": doi, "RA": "Crossref"}])
        responses.add(responses.GET, f"{CROSSREF_API}/{doi}", body=RequestsConnectionError("down"))
        result = classify_doi(doi)
        assert result.is_valid is True
        assert result.registration_agency == "Crossref"
        assert result.inferred_nmdc_category is None


# ---------------------------------------------------------------------------
# Fixture validation
# ---------------------------------------------------------------------------


def test_fixture_coverage(test_cases: list[dict[str, object]]) -> None:
    """Verify the fixture has expected breadth."""
    valid = [tc for tc in test_cases if tc["valid"]]
    invalid = [tc for tc in test_cases if not tc["valid"]]
    assert len(valid) >= 20
    assert len(invalid) >= 4

    agencies = {tc["registration_agency"] for tc in valid}
    assert {"Crossref", "DataCite"} <= agencies

    categories = {tc["expected_nmdc_category"] for tc in valid}
    assert {"publication_doi", "dataset_doi", "award_doi"} <= categories

    assigned_prefixes = {"10.4319", "10.1038", "10.3389", "10.1016", "10.2136", "10.17504"}
    fixture_prefixes = {str(tc["prefix"]) for tc in valid}
    assert not (assigned_prefixes - fixture_prefixes), "Missing #1597 prefixes"

    for tc in valid:
        assert tc["nmdc_entity_id"], f"Missing nmdc_entity_id for {tc['doi']}"
        assert tc["nmdc_path"], f"Missing nmdc_path for {tc['doi']}"


# ---------------------------------------------------------------------------
# Integration tests — hit real APIs, skip in CI
# ---------------------------------------------------------------------------


@integration
def test_validate_real_doi() -> None:
    """Validate a known-good DOI against the real Handle API."""
    result = validate_doi("10.1038/s41564-020-00861-0")
    assert result.is_valid is True
    assert result.handle_response_code == 1


@integration
def test_validate_bogus_doi() -> None:
    """A nonexistent DOI should fail validation."""
    result = validate_doi("10.99999/fake-doi-does-not-exist")
    assert result.is_valid is False


@integration
def test_classify_real_publication() -> None:
    """Classify a Nature journal article against real APIs."""
    result = classify_doi("10.1038/s41564-020-00861-0")
    assert result.is_valid is True
    assert result.registration_agency == "Crossref"
    assert result.resource_type == "journal-article"
    assert result.inferred_nmdc_category == "publication_doi"


@integration
def test_classify_real_dataset() -> None:
    """Classify an ESS-DIVE dataset DOI against real APIs."""
    result = classify_doi("10.15485/1729719")
    assert result.is_valid is True
    assert result.registration_agency == "DataCite"
    assert result.inferred_nmdc_category == "dataset_doi"
