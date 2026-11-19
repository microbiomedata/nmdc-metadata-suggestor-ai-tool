import json
import logging
from pathlib import Path
from typing import Any

import pytest

from nmdc_metadata_suggestor_ai_tool.schema_context import get_schema_view
from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import (
    MixsExtensions,
    get_samples,
    get_submission_fields,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(filename: str) -> dict[str, Any]:
    with (FIXTURES_DIR / filename).open() as fixture_file:
        data: dict[str, Any] = json.load(fixture_file)
        return data


def test_get_submission_fields_extracts_metadata_from_fixtures() -> None:
    submission = _load_fixture("submission_response_multiple_sample_sets.json")
    sample_set = _load_fixture("submission_sample_set.json")
    study_form = submission["study_form"]

    result = get_submission_fields(submission, sample_set)

    assert result["study_name"] == study_form["studyName"]
    assert result["description"] == study_form["description"]
    assert result["dois"] == [
        {"value": "10.1073/pnas.2004192118", "provider": "osti"},
        {"value": "10.1038/s41587-020-0718-6", "provider": None},
        {"value": "10.46936/10.25585/60013043", "provider": "jgi"},
    ]
    assert result["notes"] == study_form["notes"]
    assert result["gold_study_id"] == study_form["GOLDStudyId"]
    assert result["ncbi_bio_project"] == study_form["NCBIBioProjectId"]
    assert result["jgi_study_id"] == sample_set["multi_omics_form"]["JGIStudyId"]
    assert result["award_dois"] == sample_set["multi_omics_form"]["awardDois"]
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


def test_every_env_triad_interface_is_reachable_from_a_portal_name() -> None:
    """A dropped extension silently loses its value set, so the roster must be complete."""
    schema_view = get_schema_view()
    with_triad = {
        name
        for name in schema_view.all_classes()
        if name.endswith("Interface")
        and "env_broad_scale" in {s.name for s in schema_view.class_induced_slots(name)}
    }
    assert with_triad <= set(MixsExtensions.__members__)


@pytest.mark.parametrize(
    "spelling",
    [
        "miscellaneous natural or artificial environment",  # the portal's template key
        "miscellaneous natural or artifical environment",  # legacy misspelling
    ],
)
def test_misc_envs_resolves_from_either_spelling(spelling: str) -> None:
    assert MixsExtensions.map_to_interface_name([spelling]) == ["MiscEnvsInterface"]


def test_get_samples_extracts_samples_from_fixture() -> None:
    sample_set = _load_fixture("submission_sample_set.json")
    samples = get_samples(sample_set, "soil")

    assert isinstance(samples, list)
    assert len(samples) > 0
    assert samples == sample_set["sample_data"]["data"]["soil_data"]


@pytest.mark.parametrize(
    "sample_set_object,interface_name",
    [
        ({}, "soil"),
        ({"sample_data": {}}, "soil"),
        ({"sample_data": {"data": {}}}, "soil"),
        ({"sample_data": {"data": {"water_data": [{"samp_name": "s1"}]}}}, "soil"),
    ],
)
def test_get_samples_returns_empty_list_when_missing(
    sample_set_object: dict[str, Any], interface_name: str
) -> None:
    assert get_samples(sample_set_object, interface_name) == []


def test_wastewater_sludge_resolves_from_its_sample_slot_name() -> None:
    """Schema-only extension: no portal template, so the ETL path is its only route."""
    assert MixsExtensions.map_to_interface_name(["wastewater_sludge"]) == [
        "WastewaterSludgeInterface"
    ]


def test_get_submission_fields_reads_publication_and_award_dois() -> None:
    """A minimal portal submission: one publication DOI, one award DOI.

    Modeled on the phyllosphere study (nmdc:sty-11-e4yb9z58), the supplement eval
    case in nmdc-ai-eval. The pipeline fetches the abstract through the first DOI
    and routes the second as an award, so both must come back, in that order.
    """
    submission = {
        "study_form": {
            "studyName": "Seasonal activities of the phyllosphere microbiome",
            "description": "Leaf surface samples from perennial bioenergy crops.",
            "publicationDois": [{"value": "10.1038/s41467-023-36515-y", "provider": None}],
        }
    }
    sample_set = {
        "multi_omics_form": {
            "awardDois": [{"value": "10.46936/10.25585/60000818", "provider": "jgi"}]
        }
    }

    result = get_submission_fields(submission, sample_set)

    assert [d["value"] for d in result["dois"]] == [
        "10.1038/s41467-023-36515-y",
        "10.46936/10.25585/60000818",
    ]
    assert result["study_name"].startswith("Seasonal activities")


def test_get_submission_fields_extracts_protocol_metadata() -> None:
    submission = {"study_form": {"studyName": "Protocol Study"}}
    sample_set = {
        "multi_omics_form": {
            "mbProtocols": {
                "sampleProtocol": {
                    "url": "",
                    "doi": "10.3333/99999",
                    "name": "sample prep",
                    "description": "extraction protocol",
                },
                "acquisitionProtocol": {
                    "url": "",
                    "doi": "",
                    "name": "",
                    "description": "",
                },
                "dataProtocol": {
                    "url": "",
                    "doi": None,
                    "name": None,
                    "description": None,
                },
            }
        }
    }

    result = get_submission_fields(submission, sample_set)

    assert result["dois"] == [{"value": "10.3333/99999", "provider": None}]
    assert result["protocol_names"] == ["sample prep"]
    assert result["protocol_descs"] == ["extraction protocol"]
