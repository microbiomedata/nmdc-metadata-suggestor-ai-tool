import json
from pathlib import Path

from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import get_submission_fields

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(filename: str) -> dict:
    with (FIXTURES_DIR / filename).open() as fixture_file:
        return json.load(fixture_file)


def test_get_submission_fields_extracts_protocol_metadata_from_fixture() -> None:
    submission = _load_fixture("test_submission.json")
    study_form = submission["metadata_submission"]["studyForm"]

    result = get_submission_fields(submission)

    assert result["study_name"] == study_form["studyName"]
    assert result["description"] == study_form["description"]
    assert result["dois"] == [
        {"value": "10.1073/pnas.2004192118", "provider": "osti"},
        {"value": "10.46936/10.25585/60013043", "provider": "jgi"},
        {"value": "doi:10.3791/57343", "provider": None},
    ]
    assert result["notes"] == ""
    assert result["gold_study_id"] == ""
    assert result["jgi_study_id"] == "510963"
    assert result["protocol_names"] == [
        "proteomics lc",
        "metabolome lc",
        "lc lipid",
        "methanol water extraction",
        "nom fticr",
    ]
    assert len(result["protocol_descs"]) == 5
    assert result["mixis_extensions"] == ["SoilInterface"]
