"""Tests for DOI validation, normalization, and classification.

Uses the curated test fixture at tests/fixtures/doi_test_cases.json
and mocks all HTTP calls with the ``responses`` library.
"""

import json
from pathlib import Path

import pytest
import responses
from requests.exceptions import ConnectionError as RequestsConnectionError

from nmdc_metadata_suggestor.publication_ingestion.doi_utils import (
    CROSSREF_API,
    DATACITE_API,
    DOI_HANDLE_API,
    DOI_RA_API,
    DoiClassification,
    DoiValidation,
    classify_doi,
    detect_registration_agency,
    infer_nmdc_category,
    normalize_doi,
    validate_doi,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "doi_test_cases.json"


@pytest.fixture()
def test_cases() -> list[dict[str, object]]:
    """Load the curated DOI test cases."""
    with open(FIXTURE_PATH) as f:
        data = json.load(f)
    return data["test_cases"]


@pytest.fixture()
def valid_cases(test_cases: list[dict[str, object]]) -> list[dict[str, object]]:
    """Only test cases expected to be valid."""
    return [tc for tc in test_cases if tc["valid"]]


@pytest.fixture()
def invalid_cases(test_cases: list[dict[str, object]]) -> list[dict[str, object]]:
    """Only test cases expected to be invalid."""
    return [tc for tc in test_cases if not tc["valid"]]


# ---------------------------------------------------------------------------
# normalize_doi — pure function, no mocking needed
# ---------------------------------------------------------------------------


class TestNormalizeDoi:
    """Tests for normalize_doi()."""

    def test_bare_doi_unchanged(self) -> None:
        assert normalize_doi("10.1038/s41564-020-00861-0") == "10.1038/s41564-020-00861-0"

    def test_https_doi_org(self) -> None:
        assert normalize_doi("https://doi.org/10.1038/s41564-020-00861-0") == (
            "10.1038/s41564-020-00861-0"
        )

    def test_http_doi_org(self) -> None:
        assert normalize_doi("http://doi.org/10.1038/s41564-020-00861-0") == (
            "10.1038/s41564-020-00861-0"
        )

    def test_dx_doi_org(self) -> None:
        assert normalize_doi("https://dx.doi.org/10.1038/s41564-020-00861-0") == (
            "10.1038/s41564-020-00861-0"
        )

    def test_doi_prefix(self) -> None:
        assert normalize_doi("doi:10.1038/s41564-020-00861-0") == "10.1038/s41564-020-00861-0"

    def test_angle_brackets(self) -> None:
        assert normalize_doi("<10.1038/s41564-020-00861-0>") == "10.1038/s41564-020-00861-0"

    def test_trailing_punctuation(self) -> None:
        assert normalize_doi("10.1038/s41564-020-00861-0.") == "10.1038/s41564-020-00861-0"
        assert normalize_doi("10.1038/s41564-020-00861-0,") == "10.1038/s41564-020-00861-0"

    def test_whitespace(self) -> None:
        assert normalize_doi("  10.1038/s41564-020-00861-0  ") == "10.1038/s41564-020-00861-0"

    def test_old_format_with_parentheses(self) -> None:
        """DOI from 1985 Elsevier with parentheses in suffix."""
        assert normalize_doi("10.1016/0038-0717(85)90144-0") == "10.1016/0038-0717(85)90144-0"

    def test_case_insensitive_url(self) -> None:
        assert normalize_doi("HTTPS://DOI.ORG/10.1038/s41564-020-00861-0") == (
            "10.1038/s41564-020-00861-0"
        )

    def test_case_insensitive_doi_prefix(self) -> None:
        assert normalize_doi("DOI:10.1038/s41564-020-00861-0") == "10.1038/s41564-020-00861-0"


# ---------------------------------------------------------------------------
# validate_doi — requires Handle API mocking
# ---------------------------------------------------------------------------


class TestValidateDoi:
    """Tests for validate_doi() with mocked Handle API."""

    @responses.activate
    def test_valid_doi(self) -> None:
        doi = "10.1038/s41564-020-00861-0"
        responses.add(
            responses.GET,
            f"{DOI_HANDLE_API}/{doi}",
            json={"responseCode": 1, "handle": doi},
            status=200,
        )
        result = validate_doi(doi)
        assert isinstance(result, DoiValidation)
        assert result.is_valid is True
        assert result.handle_response_code == 1
        assert result.error is None

    @responses.activate
    def test_not_found_doi(self) -> None:
        doi = "10.99999/fake-doi-does-not-exist"
        responses.add(
            responses.GET,
            f"{DOI_HANDLE_API}/{doi}",
            json={"responseCode": 100, "handle": doi},
            status=200,
        )
        result = validate_doi(doi)
        assert result.is_valid is False
        assert result.handle_response_code == 100

    def test_malformed_doi_no_api_call(self) -> None:
        """Malformed DOIs should fail regex before making any HTTP call."""
        result = validate_doi("not-a-doi-at-all")
        assert result.is_valid is False
        assert result.error == "Malformed DOI syntax"

    def test_empty_doi(self) -> None:
        result = validate_doi("")
        assert result.is_valid is False
        assert result.error == "Malformed DOI syntax"

    @responses.activate
    def test_network_error(self) -> None:
        doi = "10.1038/s41564-020-00861-0"
        responses.add(
            responses.GET,
            f"{DOI_HANDLE_API}/{doi}",
            body=RequestsConnectionError("Network unreachable"),
        )
        result = validate_doi(doi)
        assert result.is_valid is False
        assert "Request failed" in (result.error or "")


# ---------------------------------------------------------------------------
# detect_registration_agency — requires doiRA API mocking
# ---------------------------------------------------------------------------


class TestDetectRegistrationAgency:
    """Tests for detect_registration_agency() with mocked doiRA API."""

    @responses.activate
    def test_crossref(self) -> None:
        doi = "10.1038/s41564-020-00861-0"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            json=[{"DOI": doi, "RA": "Crossref"}],
            status=200,
        )
        assert detect_registration_agency(doi) == "Crossref"

    @responses.activate
    def test_datacite(self) -> None:
        doi = "10.15485/1729719"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            json=[{"DOI": doi, "RA": "DataCite"}],
            status=200,
        )
        assert detect_registration_agency(doi) == "DataCite"

    @responses.activate
    def test_network_error_returns_none(self) -> None:
        doi = "10.1038/s41564-020-00861-0"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            body=RequestsConnectionError("Network unreachable"),
        )
        assert detect_registration_agency(doi) is None


# ---------------------------------------------------------------------------
# infer_nmdc_category — pure function, no mocking
# ---------------------------------------------------------------------------


class TestInferNmdcCategory:
    """Tests for infer_nmdc_category()."""

    # Crossref types
    def test_crossref_journal_article(self) -> None:
        assert infer_nmdc_category("Crossref", "journal-article", None) == "publication_doi"

    def test_crossref_posted_content(self) -> None:
        assert infer_nmdc_category("Crossref", "posted-content", None) == "publication_doi"

    def test_crossref_book_chapter(self) -> None:
        assert infer_nmdc_category("Crossref", "book-chapter", None) == "publication_doi"

    def test_crossref_dataset(self) -> None:
        assert infer_nmdc_category("Crossref", "dataset", None) == "dataset_doi"

    def test_crossref_grant(self) -> None:
        assert infer_nmdc_category("Crossref", "grant", None) == "award_doi"

    def test_crossref_unknown_type(self) -> None:
        assert infer_nmdc_category("Crossref", "component", None) is None

    # DataCite types
    def test_datacite_dataset(self) -> None:
        assert infer_nmdc_category("DataCite", None, "Dataset") == "dataset_doi"

    def test_datacite_journal_article(self) -> None:
        assert infer_nmdc_category("DataCite", None, "JournalArticle") == "publication_doi"

    def test_datacite_preprint(self) -> None:
        assert infer_nmdc_category("DataCite", None, "Preprint") == "publication_doi"

    def test_datacite_award(self) -> None:
        assert infer_nmdc_category("DataCite", None, "Award") == "award_doi"

    def test_datacite_dmp(self) -> None:
        assert infer_nmdc_category("DataCite", None, "OutputManagementPlan") == (
            "data_management_plan_doi"
        )

    def test_datacite_software(self) -> None:
        """Software is a valid DataCite type but has no NMDC mapping."""
        assert infer_nmdc_category("DataCite", None, "Software") is None

    # Edge cases
    def test_none_agency(self) -> None:
        assert infer_nmdc_category(None, "journal-article", None) is None

    def test_none_type(self) -> None:
        assert infer_nmdc_category("Crossref", None, None) is None


# ---------------------------------------------------------------------------
# classify_doi — requires RA + agency API mocking
# ---------------------------------------------------------------------------


class TestClassifyDoi:
    """Tests for classify_doi() with mocked APIs."""

    @responses.activate
    def test_crossref_journal_article(self) -> None:
        doi = "10.1038/s41564-020-00861-0"
        # RA detection
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            json=[{"DOI": doi, "RA": "Crossref"}],
        )
        # Crossref metadata
        responses.add(
            responses.GET,
            f"{CROSSREF_API}/{doi}",
            json={
                "message": {
                    "type": "journal-article",
                    "publisher": "Springer Science and Business Media LLC",
                }
            },
        )
        result = classify_doi(doi)
        assert isinstance(result, DoiClassification)
        assert result.is_valid is True
        assert result.registration_agency == "Crossref"
        assert result.resource_type == "journal-article"
        assert result.inferred_nmdc_category == "publication_doi"
        assert result.prefix == "10.1038"

    @responses.activate
    def test_crossref_posted_content(self) -> None:
        """Preprints (bioRxiv/medRxiv) are Crossref 'posted-content'."""
        doi = "10.1101/2022.12.12.520098"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            json=[{"DOI": doi, "RA": "Crossref"}],
        )
        responses.add(
            responses.GET,
            f"{CROSSREF_API}/{doi}",
            json={
                "message": {
                    "type": "posted-content",
                    "publisher": "Cold Spring Harbor Laboratory",
                }
            },
        )
        result = classify_doi(doi)
        assert result.is_valid is True
        assert result.resource_type == "posted-content"
        assert result.inferred_nmdc_category == "publication_doi"

    @responses.activate
    def test_crossref_book_chapter(self) -> None:
        """SSSA book chapter DOI."""
        doi = "10.2136/sssabookser5.3.c16"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            json=[{"DOI": doi, "RA": "Crossref"}],
        )
        responses.add(
            responses.GET,
            f"{CROSSREF_API}/{doi}",
            json={
                "message": {
                    "type": "book-chapter",
                    "publisher": "Soil Science Society of America",
                }
            },
        )
        result = classify_doi(doi)
        assert result.is_valid is True
        assert result.resource_type == "book-chapter"
        assert result.inferred_nmdc_category == "publication_doi"

    @responses.activate
    def test_datacite_dataset(self) -> None:
        """ESS-DIVE dataset DOI — should classify as dataset_doi, not publication_doi."""
        doi = "10.15485/1729719"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            json=[{"DOI": doi, "RA": "DataCite"}],
        )
        responses.add(
            responses.GET,
            f"{DATACITE_API}/{doi}",
            json={
                "data": {
                    "attributes": {
                        "types": {
                            "resourceType": "Dataset",
                            "resourceTypeGeneral": "Dataset",
                        },
                        "publisher": "ESS-DIVE",
                    }
                }
            },
        )
        result = classify_doi(doi)
        assert result.is_valid is True
        assert result.registration_agency == "DataCite"
        assert result.resource_type_general == "Dataset"
        assert result.inferred_nmdc_category == "dataset_doi"
        assert result.prefix == "10.15485"

    @responses.activate
    def test_datacite_award(self) -> None:
        """EMSL award DOI — should classify as award_doi."""
        doi = "10.46936/jejc.proj.2014.48483/60005501"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            json=[{"DOI": doi, "RA": "DataCite"}],
        )
        responses.add(
            responses.GET,
            f"{DATACITE_API}/{doi}",
            json={
                "data": {
                    "attributes": {
                        "types": {
                            "resourceType": "Project",
                            "resourceTypeGeneral": "Award",
                        },
                        "publisher": "EMSL",
                    }
                }
            },
        )
        result = classify_doi(doi)
        assert result.is_valid is True
        assert result.inferred_nmdc_category == "award_doi"

    @responses.activate
    def test_datacite_protocols_io(self) -> None:
        """protocols.io DOI — DataCite, may have no standard type mapping."""
        doi = "10.17504/protocols.io.kxygxyydkl8j/v1"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            json=[{"DOI": doi, "RA": "DataCite"}],
        )
        responses.add(
            responses.GET,
            f"{DATACITE_API}/{doi}",
            json={
                "data": {
                    "attributes": {
                        "types": {
                            "resourceType": "Protocol",
                            "resourceTypeGeneral": "Text",
                        },
                        "publisher": "protocols.io",
                    }
                }
            },
        )
        result = classify_doi(doi)
        assert result.is_valid is True
        assert result.registration_agency == "DataCite"
        # Text maps to publication_doi — this is correct per the type tables
        assert result.inferred_nmdc_category == "publication_doi"

    def test_malformed_doi(self) -> None:
        """Malformed DOIs should fail without making any API calls."""
        result = classify_doi("not-a-doi-at-all")
        assert result.is_valid is False
        assert result.error == "Malformed DOI syntax"

    def test_empty_doi(self) -> None:
        result = classify_doi("")
        assert result.is_valid is False
        assert result.error == "Malformed DOI syntax"

    @responses.activate
    def test_unknown_ra(self) -> None:
        """DOIs from other RAs (mEDRA, etc.) should be valid but unclassified."""
        doi = "10.1234/some-medra-doi"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            json=[{"DOI": doi, "RA": "mEDRA"}],
        )
        result = classify_doi(doi)
        assert result.is_valid is True
        assert result.registration_agency == "mEDRA"
        assert result.inferred_nmdc_category is None

    @responses.activate
    def test_ra_detection_failure(self) -> None:
        """If RA detection fails, DOI should be marked invalid."""
        doi = "10.99999/fake-doi-does-not-exist"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            body=RequestsConnectionError("Network unreachable"),
        )
        result = classify_doi(doi)
        assert result.is_valid is False
        assert "Could not detect registration agency" in (result.error or "")

    @responses.activate
    def test_crossref_api_error_still_valid(self) -> None:
        """If Crossref API fails after RA is detected, DOI is still valid."""
        doi = "10.1038/s41564-020-00861-0"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            json=[{"DOI": doi, "RA": "Crossref"}],
        )
        responses.add(
            responses.GET,
            f"{CROSSREF_API}/{doi}",
            body=RequestsConnectionError("Crossref down"),
        )
        result = classify_doi(doi)
        assert result.is_valid is True
        assert result.registration_agency == "Crossref"
        assert "Crossref API error" in (result.error or "")
        assert result.inferred_nmdc_category is None


# ---------------------------------------------------------------------------
# Fixture-driven tests — verify coverage across all assigned prefixes
# ---------------------------------------------------------------------------


class TestFixtureCoverage:
    """Verify the test fixture covers expected DOI categories and prefixes."""

    def test_fixture_loads(self, test_cases: list[dict[str, object]]) -> None:
        assert len(test_cases) >= 25

    def test_has_valid_and_invalid(self, test_cases: list[dict[str, object]]) -> None:
        valid = [tc for tc in test_cases if tc["valid"]]
        invalid = [tc for tc in test_cases if not tc["valid"]]
        assert len(valid) >= 20
        assert len(invalid) >= 4

    def test_both_registration_agencies(self, valid_cases: list[dict[str, object]]) -> None:
        agencies = {tc["registration_agency"] for tc in valid_cases}
        assert "Crossref" in agencies
        assert "DataCite" in agencies

    def test_all_nmdc_doi_categories(self, valid_cases: list[dict[str, object]]) -> None:
        categories = {tc["expected_nmdc_category"] for tc in valid_cases}
        assert "publication_doi" in categories
        assert "dataset_doi" in categories
        assert "award_doi" in categories

    def test_issue_1597_prefixes(self, valid_cases: list[dict[str, object]]) -> None:
        """All 6 assigned prefixes for issue #1597 should be in the fixture."""
        assigned_prefixes = {"10.4319", "10.1038", "10.3389", "10.1016", "10.2136", "10.17504"}
        fixture_prefixes = {str(tc["prefix"]) for tc in valid_cases}
        missing = assigned_prefixes - fixture_prefixes
        assert not missing, f"Missing assigned prefixes: {missing}"

    def test_all_valid_have_nmdc_entity(self, valid_cases: list[dict[str, object]]) -> None:
        """Every valid test case should have an NMDC entity ID."""
        for tc in valid_cases:
            assert tc["nmdc_entity_id"], f"Missing nmdc_entity_id for {tc['doi']}"

    def test_all_valid_have_nmdc_path(self, valid_cases: list[dict[str, object]]) -> None:
        """Every valid test case should have an NMDC path."""
        for tc in valid_cases:
            assert tc["nmdc_path"], f"Missing nmdc_path for {tc['doi']}"


# ---------------------------------------------------------------------------
# Graceful out-of-scope handling — dataset/award DOIs in publication context
# ---------------------------------------------------------------------------


class TestGracefulOutOfScope:
    """Verify that dataset and award DOIs classify correctly so
    publication-focused code can filter them gracefully."""

    @responses.activate
    def test_dataset_doi_not_publication(self) -> None:
        """A dataset DOI should NOT be inferred as publication_doi."""
        doi = "10.15485/1729719"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            json=[{"DOI": doi, "RA": "DataCite"}],
        )
        responses.add(
            responses.GET,
            f"{DATACITE_API}/{doi}",
            json={
                "data": {
                    "attributes": {
                        "types": {
                            "resourceType": "Dataset",
                            "resourceTypeGeneral": "Dataset",
                        },
                        "publisher": "ESS-DIVE",
                    }
                }
            },
        )
        result = classify_doi(doi)
        assert result.inferred_nmdc_category != "publication_doi"
        assert result.inferred_nmdc_category == "dataset_doi"

    @responses.activate
    def test_award_doi_not_publication(self) -> None:
        """An award DOI should NOT be inferred as publication_doi."""
        doi = "10.46936/jejc.proj.2014.48483/60005501"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            json=[{"DOI": doi, "RA": "DataCite"}],
        )
        responses.add(
            responses.GET,
            f"{DATACITE_API}/{doi}",
            json={
                "data": {
                    "attributes": {
                        "types": {
                            "resourceType": "Project",
                            "resourceTypeGeneral": "Award",
                        },
                        "publisher": "EMSL",
                    }
                }
            },
        )
        result = classify_doi(doi)
        assert result.inferred_nmdc_category != "publication_doi"
        assert result.inferred_nmdc_category == "award_doi"

    @responses.activate
    def test_jgi_dataset_doi_not_publication(self) -> None:
        """JGI dataset DOI should classify as dataset_doi."""
        doi = "10.25585/1487763"
        responses.add(
            responses.GET,
            f"{DOI_RA_API}/{doi}",
            json=[{"DOI": doi, "RA": "DataCite"}],
        )
        responses.add(
            responses.GET,
            f"{DATACITE_API}/{doi}",
            json={
                "data": {
                    "attributes": {
                        "types": {
                            "resourceType": "Dataset",
                            "resourceTypeGeneral": "Dataset",
                        },
                        "publisher": "Joint Genome Institute",
                    }
                }
            },
        )
        result = classify_doi(doi)
        assert result.inferred_nmdc_category == "dataset_doi"
