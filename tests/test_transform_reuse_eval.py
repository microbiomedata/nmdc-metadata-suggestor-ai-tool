"""Tests for the issue 177 transform-reuse eval: scorer, and runner with a stubbed agent."""

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from nmdc_metadata_suggestor_ai_tool.evaluation import transform_reuse
from nmdc_metadata_suggestor_ai_tool.evaluation.mapper_comparison import (
    compare_mappings,
    compare_values,
    confidence_counts,
    count_transform_errors,
    run_cost,
)
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.transform_spec import normalize_header
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    ColumnMapping,
    MetadataMapperOutput,
    ValueConversion,
)
from nmdc_metadata_suggestor_ai_tool.models.reusable_transform import ReusableTransform

PHAGE_CSV = Path(__file__).parent / "fixtures" / "PRJEB13831_Phage_metadata.csv"

DATE = ValueConversion(type="date_format", description="M/D/Y", expression="%m/%d/%Y")


def column(name: str, slot: str | None, conversion: ValueConversion | None = None) -> ColumnMapping:
    if slot is None:
        return ColumnMapping(
            source_column=name, source_file_id="f", confidence="cant_place", reason="none"
        )
    return ColumnMapping(
        source_column=name,
        source_file_id="f",
        mixs_extension="Water",
        nmdc_candidate_slots=[slot],
        confidence="high",
        reason="fits",
        conversion=conversion,
    )


class TestCompareMappings:
    def test_agreement(self) -> None:
        reference = MetadataMapperOutput(
            high_confidence=[
                column("date", "collection_date", DATE),
                column("site", "geo_loc_name"),
            ],
            cant_place=[column("notes", None)],
        )
        candidate = MetadataMapperOutput(
            high_confidence=[
                column(
                    "date",
                    "collection_date",
                    ValueConversion(type="date_format", description="D/M/Y", expression="%d/%m/%Y"),
                ),
                column("site", "samp_name"),
                column("extra", "depth"),
            ],
            cant_place=[column("notes", None)],
        )
        result = compare_mappings(reference, candidate)
        assert result["columns_compared"] == 3
        assert result["slot_agreement"] == pytest.approx(2 / 3, abs=1e-3)
        assert result["conversion_agreement"] == 0.0
        assert result["only_in_candidate"] == ["extra"]
        assert result["slot_disagreements"] == [
            {"column": "site", "reference": "geo_loc_name", "candidate": "samp_name"}
        ]

    def test_counts_and_cost(self) -> None:
        output = MetadataMapperOutput(
            high_confidence=[column("a", "x")],
            cant_place=[column("b", None)],
            run_health={"num_turns": 4, "total_cost_usd": 0.5},
        )
        assert confidence_counts(output) == {"high": 1, "review": 0, "cant_place": 1}
        assert run_cost(output)["num_turns"] == 4
        assert run_cost(output)["input_tokens"] is None


def test_compare_values() -> None:
    reference: list[dict[str, Any]] = [{"a": "1", "b": "x"}, {"a": "2"}]
    candidate: list[dict[str, Any]] = [
        {"a": "1", "b": "y"},
        {"a": "2", "_transform_errors": ["boom"]},
    ]
    result = compare_values(reference, candidate, {"a", "b", "c"})
    assert result["cells_compared"] == 3
    assert result["value_agreement"] == pytest.approx(2 / 3, abs=1e-3)
    assert result["slots_with_differences"] == {"b": 1}
    assert count_transform_errors(candidate) == 1


def test_split_csv_perturbs_later_headers(tmp_path: Path) -> None:
    earlier, later, headers = transform_reuse.split_csv(PHAGE_CSV, tmp_path, perturb_headers=True)
    earlier_headers, earlier_rows = transform_reuse.read_rows(earlier)
    later_headers, later_rows = transform_reuse.read_rows(later)
    assert len(earlier_rows) + len(later_rows) == 18
    assert later_headers == headers
    assert "Sample Collection Start Date" in later_headers
    # Spelled differently, but the same columns once normalized.
    assert later_headers != earlier_headers
    assert [normalize_header(h) for h in later_headers] == [
        normalize_header(h) for h in earlier_headers
    ]


def stub_agent(
    client: object,
    path: Path,
    file_id: str,
    extensions: list[str],
    transform: ReusableTransform | None = None,
) -> tuple[MetadataMapperOutput, float]:
    """Stand-in for the mapper agent: a fixed mapping, keyed on the file's own spellings."""
    headers, _ = transform_reuse.read_rows(path)
    by_key = {normalize_header(h).replace(" ", "_"): h for h in headers}

    def mapped(key: str, slot: str, conversion: ValueConversion | None = None) -> ColumnMapping:
        m = column(by_key[key], slot, conversion)
        return m.model_copy(update={"source_file_id": file_id})

    output = MetadataMapperOutput(
        high_confidence=[
            mapped("sample_collection_start_date", "collection_date", DATE),
            mapped("geo_loc_name_country", "geo_loc_name"),
            mapped("specimen_collector_sample_id", "samp_name"),
        ],
        cant_place=[
            column(by_key["organism"], None).model_copy(update={"source_file_id": file_id})
        ],
        run_health={"num_turns": 2 if transform else 6, "input_tokens": 100 if transform else 900},
    )
    return output, 1.0


@pytest.mark.parametrize("perturb", [False, True])
def test_runner_end_to_end_with_stub_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, perturb: bool
) -> None:
    monkeypatch.setattr(transform_reuse, "run_agent", stub_agent)
    monkeypatch.setattr(transform_reuse, "LLMClient", lambda **kwargs: None)
    argv = ["transform_reuse", "--reps", "2", "--out-dir", str(tmp_path)]
    if perturb:
        argv.append("--perturb-headers")
    monkeypatch.setattr(sys, "argv", argv)

    transform_reuse.main()

    (run_dir,) = tmp_path.iterdir()
    results = yaml.safe_load((run_dir / "results.yaml").read_text())
    assert results["transform"]["overlap_with_later"] == 1.0
    assert results["summary"]["with_transform"]["slot_agreement"] == 1.0
    assert results["summary"]["with_transform"]["input_tokens"] == 100
    assert results["summary"]["cold"]["input_tokens"] == 900
    assert results["summary"]["transform_only"]["value_agreement"] == 1.0
    assert results["transform_only"]["parity_with_apply_mappings"] is True
    assert results["transform_only"]["covered"] == 3
    assert results["consistency"]["cold"]["pairs"] == 1
    assert (run_dir / "learned_transform.yaml").exists()
    assert "| with_transform |" in (run_dir / "report.md").read_text()
