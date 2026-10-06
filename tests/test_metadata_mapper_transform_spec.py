"""Tests for compiling mapper output into reusable linkml-map transforms."""

import json
from pathlib import Path
from typing import Literal

import pytest

from nmdc_metadata_suggestor_ai_tool.metadata_mapper.apply import apply_mappings
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.transform_spec import (
    TransformLibrary,
    build_prior_transform_context,
    compile_transform,
    hidden_slot_names,
    mapper_output_from_transform,
    match_transform,
    normalize_header,
    rebase_transform,
    run_transform,
)
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    ColumnMapping,
    MetadataMapperOutput,
    SourceFile,
    ValueConversion,
)

FILE_ID = "f1"
HEADERS = [
    "Sample Date",
    "Depth (ft)",
    "biotic rel",
    "Lat",
    "Lon",
    "Tags",
    "Temp F",
    "notes",
    "class",
]


def mapping(
    column: str,
    slot: str,
    conversion: ValueConversion | None = None,
    combine: list[str] | None = None,
    confidence: Literal["high", "review", "cant_place"] = "high",
) -> ColumnMapping:
    return ColumnMapping(
        source_column=column,
        combine_columns=combine or [],
        source_file_id=FILE_ID,
        mixs_extension="Water",
        nmdc_candidate_slots=[slot],
        confidence=confidence,
        reason=f"{column} is {slot}",
        conversion=conversion,
    )


@pytest.fixture
def mapper_output() -> MetadataMapperOutput:
    return MetadataMapperOutput(
        source_files=[SourceFile(file_id=FILE_ID, display_name="samples.csv")],
        high_confidence=[
            mapping(
                "Sample Date",
                "collection_date",
                ValueConversion(type="date_format", description="M/D/Y", expression="%m/%d/%Y"),
            ),
            mapping(
                "Depth (ft)",
                "depth",
                ValueConversion(type="unit", description="ft → m", expression="0.3048"),
            ),
            mapping(
                "biotic rel",
                "biotic_relationship",
                ValueConversion(
                    type="enum_map",
                    description="normalize",
                    expression=json.dumps({"Free Living": "free living"}),
                ),
            ),
            mapping(
                "Lat",
                "lat_lon",
                ValueConversion(
                    type="custom",
                    description="join",
                    expression="f\"{values['Lat']} {values['Lon']}\"",
                ),
                combine=["Lon"],
            ),
            mapping(
                "Tags",
                "misc_param",
                ValueConversion(type="split", description="commas", expression=","),
            ),
            mapping(
                "class",
                "samp_name",
                ValueConversion(type="none", description="as is"),
            ),
        ],
        needs_review=[
            mapping(
                "Temp F",
                "temp",
                ValueConversion(
                    type="custom",
                    description="F → C",
                    expression="str(round((float(value) - 32) * 5 / 9, 2))",
                ),
                confidence="review",
            ),
        ],
        cant_place=[
            ColumnMapping(
                source_column="notes",
                source_file_id=FILE_ID,
                confidence="cant_place",
                reason="free text",
            )
        ],
    )


ROWS = [
    {
        "Sample Date": "1/26/2016",
        "Depth (ft)": "10",
        "biotic rel": "Free Living",
        "Lat": "45.1",
        "Lon": "-75.2",
        "Tags": "a, b,c",
        "Temp F": "212",
        "notes": "x",
        "class": "S1",
    },
    {
        # Blank cells, a bad date, an unmapped enum value, a non-numeric temperature.
        "Sample Date": "2016-01-26",
        "Depth (ft)": "",
        "biotic rel": "Parasite",
        "Lat": "45.1",
        "Lon": "",
        "Tags": "",
        "Temp F": "warm",
        "notes": "",
        "class": "S2",
    },
]


def slot_values(row: dict, slots: set[str]) -> dict:
    return {k: v for k, v in row.items() if k in slots}


class TestCompile:
    def test_hidden_slots_are_identifiers(self) -> None:
        slots = hidden_slot_names(["Depth (ft)", "class", "1st", "a b", "a-b"])
        assert slots == {
            "Depth (ft)": "src_depth_ft",
            "class": "src_col_class",
            "1st": "src_col_1st",
            "a b": "src_a_b",
            "a-b": "src_a_b_2",
        }

    def test_source_schema_keeps_header_spelling(self, mapper_output: MetadataMapperOutput) -> None:
        t = compile_transform(mapper_output, HEADERS, "demo", source_file_id=FILE_ID)
        assert list(t.source_schema["slots"]) == HEADERS
        assert t.source_schema["classes"]["Row"]["slots"] == HEADERS

    def test_spec_uses_linkml_map_idioms(self, mapper_output: MetadataMapperOutput) -> None:
        t = compile_transform(mapper_output, HEADERS, "demo", source_file_id=FILE_ID)
        assert list(t.spec["class_derivations"]) == ["WaterInterface"]
        assert t.spec["target_schema"]["name"] == "nmdc_submission_schema"
        assert t.spec["source_schema"]["source_file"] == "source_schema.yaml"
        derivations = t.spec["class_derivations"]["WaterInterface"]["slot_derivations"]
        assert derivations["samp_name"]["populated_from"] == "class"
        assert derivations["biotic_relationship"]["value_mappings"] == {
            "Free Living": {"value": "free living"}
        }
        assert derivations["src_sample_date"] == {
            "populated_from": "Sample Date",
            "missing_values": [""],
            "hide": True,
        }
        assert (
            derivations["collection_date"]["expr"]
            == "iso_date(slot('src_sample_date'), '%m/%d/%Y')"
        )
        assert derivations["depth"]["expr"] == "scale(slot('src_depth_ft'), '0.3048')"
        # Hidden copies come before the slots that read them.
        names = list(derivations)
        assert names.index("src_depth_ft") < names.index("depth")
        assert "notes" not in {m.source_column for m in t.mappings}

    def test_one_class_derivation_per_interface(self, mapper_output: MetadataMapperOutput) -> None:
        mapper_output.high_confidence[0].mixs_extension = "Soil"
        t = compile_transform(mapper_output, HEADERS, "demo", source_file_id=FILE_ID)
        assert set(t.spec["class_derivations"]) == {"SoilInterface", "WaterInterface"}
        first = run_transform(t, ROWS)[0]
        assert first["collection_date"] == "2016-01-26"
        assert first["depth"] == "3.048"

    def test_other_files_are_left_out(self, mapper_output: MetadataMapperOutput) -> None:
        mapper_output.high_confidence[0].source_file_id = "other"
        t = compile_transform(mapper_output, HEADERS, "demo", source_file_id=FILE_ID)
        derivations = t.spec["class_derivations"]["WaterInterface"]["slot_derivations"]
        assert "collection_date" not in derivations

    def test_dotted_column_names_are_refused(self, mapper_output: MetadataMapperOutput) -> None:
        mapper_output.high_confidence[-1].source_column = "class.id"
        with pytest.raises(ValueError, match="containing '.'"):
            compile_transform(mapper_output, [*HEADERS, "class.id"], "demo")


class TestRun:
    def test_matches_apply_mappings(self, mapper_output: MetadataMapperOutput) -> None:
        t = compile_transform(mapper_output, HEADERS, "demo", source_file_id=FILE_ID)
        slots = {m.nmdc_candidate_slots[0] for m in t.mappings}
        expected = apply_mappings(mapper_output, ROWS, source_file_id=FILE_ID)
        actual = run_transform(t, ROWS)
        for exp, act in zip(expected, actual, strict=True):
            assert slot_values(act, slots) == slot_values(exp, slots)

    def test_converted_values(self, mapper_output: MetadataMapperOutput) -> None:
        t = compile_transform(mapper_output, HEADERS, "demo", source_file_id=FILE_ID)
        first, second = run_transform(t, ROWS)
        assert first == {
            "collection_date": "2016-01-26",
            "depth": "3.048",
            "biotic_relationship": "free living",
            "lat_lon": "45.1 -75.2",
            "misc_param": "a; b; c",
            "samp_name": "S1",
            "temp": "100.0",
        }
        # Bad cells keep their raw value and are reported; blank cells are dropped.
        assert second["collection_date"] == "2016-01-26"
        assert second["biotic_relationship"] == "Parasite"
        assert second["temp"] == "warm"
        assert "depth" not in second and "misc_param" not in second
        errors = " | ".join(second["_transform_errors"])
        assert "date_format" in errors
        assert "'Parasite' has no mapping" in errors
        assert "custom transform failed" in errors
        assert "combine columns have no value" in errors

    def test_library_round_trip(self, mapper_output: MetadataMapperOutput, tmp_path: Path) -> None:
        library = TransformLibrary(tmp_path)
        t = compile_transform(mapper_output, HEADERS, "demo", source_file_id=FILE_ID)
        folder = library.save(t)
        assert sorted(p.name for p in folder.iterdir()) == [
            "mapper.yaml",
            "source_schema.yaml",
            "transform.yaml",
        ]
        loaded = library.load("demo")
        assert loaded == t
        assert run_transform(loaded, ROWS) == run_transform(t, ROWS)


class TestMatching:
    def test_match_ignores_case_and_spacing(self, mapper_output: MetadataMapperOutput) -> None:
        t = compile_transform(mapper_output, HEADERS, "demo", source_file_id=FILE_ID)
        headers = [h.upper() for h in HEADERS if h != "notes"] + ["extra"]
        match = match_transform(headers, [t])
        assert match is not None
        assert match.missing_columns == ["notes"]
        assert match.new_columns == ["extra"]
        assert match.overlap == pytest.approx(8 / 9)

    def test_match_ignores_punctuation(self) -> None:
        assert normalize_header("Sample_ID") == normalize_header(" sample-id ") == "sample id"

    def test_no_match_below_threshold(self, mapper_output: MetadataMapperOutput) -> None:
        t = compile_transform(mapper_output, HEADERS, "demo", source_file_id=FILE_ID)
        assert match_transform(["Sample Date", "x", "y"], [t]) is None

    def test_rebased_transform_runs_on_new_spelling(
        self, mapper_output: MetadataMapperOutput
    ) -> None:
        t = compile_transform(mapper_output, HEADERS, "demo", source_file_id=FILE_ID)
        upper_rows = [{k.upper(): v for k, v in row.items()} for row in ROWS]
        rebased = rebase_transform(t, list(upper_rows[0]))
        assert list(rebased.source_schema["slots"]) == list(upper_rows[0])
        # The combine expression is respelled along with the columns it reads.
        lat_lon = next(m for m in rebased.mappings if m.combine_columns)
        assert lat_lon.conversion is not None
        assert lat_lon.conversion.expression == "f\"{values['LAT']} {values['LON']}\""
        for new, old in zip(
            run_transform(rebased, upper_rows), run_transform(t, ROWS), strict=True
        ):
            # Error messages name columns as each file spells them; compare everything else.
            assert len(new.pop("_transform_errors", [])) == len(old.pop("_transform_errors", []))
            assert new == old

    def test_mapper_output_from_transform(self, mapper_output: MetadataMapperOutput) -> None:
        t = compile_transform(mapper_output, HEADERS, "demo", source_file_id=FILE_ID)
        new_file = SourceFile(file_id="f2", display_name="next.csv")
        headers = [h for h in HEADERS if h != "Lon"] + ["salinity"]
        output, uncovered = mapper_output_from_transform(t, new_file, headers)
        mapped = {m.source_column for m in output.high_confidence + output.needs_review}
        # lat_lon needs Lon, which is gone, so Lat is not covered either.
        assert "Lat" not in mapped
        assert set(uncovered) == {"Lat", "notes", "salinity"}
        assert {m.source_file_id for m in output.high_confidence} == {"f2"}
        assert [m.source_column for m in output.needs_review] == ["Temp F"]

    def test_prior_context_lists_mappings(self, mapper_output: MetadataMapperOutput) -> None:
        t = compile_transform(mapper_output, HEADERS, "demo", source_file_id=FILE_ID)
        match = match_transform([*HEADERS, "salinity"], [t])
        assert match is not None
        text = build_prior_transform_context(match)
        assert "'demo'" in text
        assert '"slot": "collection_date"' in text
        assert "does not cover: salinity" in text


def test_run_health_hidden_from_agent_schema() -> None:
    assert "run_health" not in MetadataMapperOutput.model_json_schema()["properties"]
    output = MetadataMapperOutput(run_health={"num_turns": 3})
    assert "run_health" not in output.model_dump()
