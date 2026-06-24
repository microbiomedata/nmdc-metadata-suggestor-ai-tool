import json
import logging
from pathlib import Path

import pytest

from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import (
    MixsExtensions,
    get_submission_fields,
)

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
    assert result["mixs_extensions"] == ["SoilInterface"]


def test_map_to_interface_name_accepts_enum_value() -> None:
    """A package value such as "soil" maps to its interface class name."""
    assert MixsExtensions.map_to_interface_name(["soil"]) == ["SoilInterface"]


def test_map_to_interface_name_accepts_enum_name() -> None:
    """A class name such as "SoilInterface" is accepted and returned as-is.

    This is the new capability #93 added to support an explicit interface tab.
    """
    assert MixsExtensions.map_to_interface_name(["SoilInterface"]) == ["SoilInterface"]


def test_map_to_interface_name_skips_empty_strings() -> None:
    """Empty (or falsy) entries are skipped, not mapped."""
    assert MixsExtensions.map_to_interface_name(["", "soil"]) == ["SoilInterface"]


def test_map_to_interface_name_skips_and_warns_on_unrecognized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unrecognized entries are dropped and logged at WARNING level."""
    with caplog.at_level(logging.WARNING):
        result = MixsExtensions.map_to_interface_name(["frobnicate"])
    assert result == []
    assert "Unrecognized MIxS extension 'frobnicate'" in caplog.text


def test_map_to_interface_name_preserves_order_across_mixed_forms() -> None:
    """A mix of value and name forms is normalized to class names in input order."""
    result = MixsExtensions.map_to_interface_name(["water", "SoilInterface", "air"])
    assert result == ["WaterInterface", "SoilInterface", "AirInterface"]
