import json
from pathlib import Path

from nmdc_metadata_suggestor.utils.submission_parser import get_submission_fields


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(filename: str) -> dict:
    with (FIXTURES_DIR / filename).open() as fixture_file:
        return json.load(fixture_file)


def test_get_submission_fields_extracts_protocol_metadata_from_fixture() -> None:
    submission = _load_fixture("0a433718-5c5b-4806-825d-a0d2a1cf850e.json")

    result = get_submission_fields(submission)

    assert result["study_name"] == "Bea's Cool Submission with Protocols for Testing Automation"
    assert result["description"] == "MS automation squad is going to retrieve this submission info programmatically"
    assert result["dois"] == []
    assert result["award_dois"] == []
    assert result["protocol_dois"] == ["doi:10.3791/57343", "doi:10.3791/57343", "doi:10.3791/57343"]
    assert result["protocol_names"] == [
        "proteomics lc",
        "metabolome lc",
        "lc lipid",
        "methanol water extraction",
        "nom fticr",
    ]
    assert len(result["protocol_descs"]) == 5


def test_get_submission_fields_handles_null_protocol_sections_and_extracts_dois() -> None:
    doi_rich_submission = _load_fixture("696ce519-d88a-445e-8a07-88d77441ca73.json")
    sparse_submission = _load_fixture("52098ce7-0e09-42d2-97bc-67f4d9004f0c.json")

    doi_rich_result = get_submission_fields(doi_rich_submission)
    sparse_result = get_submission_fields(sparse_submission)

    assert doi_rich_result["dois"] == ["10.46936/10.25585/60012567"]
    assert doi_rich_result["award_dois"] == ["10.46936/10.25585/60012567"]
    assert doi_rich_result["jgi_study_id"] == "510488"
    assert doi_rich_result["protocol_dois"] == []
    assert doi_rich_result["protocol_descs"] == []
    assert doi_rich_result["protocol_names"] == []

    assert sparse_result["study_name"] == ""
    assert sparse_result["dois"] == []
    assert sparse_result["award_dois"] == []
    assert sparse_result["protocol_dois"] == []
    assert sparse_result["protocol_descs"] == []
    assert sparse_result["protocol_names"] == []
