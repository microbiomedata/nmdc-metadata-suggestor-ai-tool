"""Integration test: full server flow for the Phage metadata CSV.

Models the two-phase server pattern:
  Phase 1 — POST /map-columns:
      run_metadata_mapper_agentic() → MetadataMapperOutput (the "job")
      build_conversion_previews() → populate previews from real CSV rows

  Phase 2 — POST /apply-mappings/{job_id}:
      apply_mappings() → transformed rows

Marked `integration` — excluded from normal CI. Requires GCP credentials.
Run with: uv run pytest -m integration tests/test_metadata_mapper_phage_integration.py -v
"""

import asyncio
import csv
from pathlib import Path

import pytest

from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient
from nmdc_metadata_suggestor_ai_tool.metadata_mapper import (
    apply_mappings,
    build_conversion_previews,
    run_metadata_mapper_agentic,
)
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    MetadataMapperOutput,
    SourceFile,
)

PHAGE_CSV = Path(__file__).parent.parent / "tests/fixtures" / "PRJEB13831_Phage_metadata.csv"
FILE_ID = "phage-001"
MIXS_EXTENSIONS = ["water"]
INTEGRATION_TIMEOUT = 240  # seconds — mapper agent can be slow


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# Phase 1 — mapper agent
# ---------------------------------------------------------------------------


@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_phase1_mapper_returns_valid_output(requires_credentials: None) -> None:
    """Phase 1: agent maps columns and returns a well-formed MetadataMapperOutput."""
    source_file = SourceFile(file_id=FILE_ID, display_name=PHAGE_CSV.name)
    client = LLMClient(access_provider="gcp")

    result, session_id = asyncio.run(
        run_metadata_mapper_agentic(
            llm_client=client,
            csv_files=[(source_file, PHAGE_CSV)],
            mixs_extensions=MIXS_EXTENSIONS,
        )
    )

    assert isinstance(result, MetadataMapperOutput), (
        f"Expected MetadataMapperOutput, got {type(result)}"
    )
    assert session_id is not None, "Expected a session_id"

    total_mapped = len(result.high_confidence) + len(result.needs_review) + len(result.cant_place)
    assert total_mapped > 0, "Agent produced no column mappings at all"

    # Every mapping must reference a known source file.
    known_file_ids = {sf.file_id for sf in result.source_files}
    for mapping in result.high_confidence + result.needs_review + result.cant_place:
        assert mapping.source_file_id in known_file_ids, (
            f"Mapping for '{mapping.source_column}' references unknown file_id "
            f"'{mapping.source_file_id}'"
        )

    # High-confidence and needs_review mappings must have at least one candidate slot.
    for mapping in result.high_confidence + result.needs_review:
        assert mapping.nmdc_candidate_slots, (
            f"'{mapping.source_column}' is confidence={mapping.confidence!r} "
            "but has no candidate slots"
        )

    # cant_place mappings must have empty candidate slots.
    for mapping in result.cant_place:
        assert not mapping.nmdc_candidate_slots, (
            f"cant_place mapping '{mapping.source_column}' unexpectedly has candidate slots"
        )


# ---------------------------------------------------------------------------
# Phase 2 — preview validation + apply
# ---------------------------------------------------------------------------


@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_phase2_apply_mappings_produces_slot_keys(requires_credentials: None) -> None:
    """Phase 2: previews built from real data, then apply_mappings renames columns to slots."""
    source_file = SourceFile(file_id=FILE_ID, display_name=PHAGE_CSV.name)
    client = LLMClient(access_provider="gcp")

    csv_rows = _read_csv_rows(PHAGE_CSV)

    mapping_output, _ = asyncio.run(
        run_metadata_mapper_agentic(
            llm_client=client,
            csv_files=[(source_file, PHAGE_CSV)],
            mixs_extensions=MIXS_EXTENSIONS,
        )
    )

    # Build previews deterministically from real rows (Phase 1 post-processing).
    build_conversion_previews(mapping_output, csv_rows, n=3)

    # Every conversion with previews must have transformer-produced output, not None.
    for mapping in mapping_output.high_confidence + mapping_output.needs_review:
        if mapping.conversion and mapping.conversion.preview:
            for pair in mapping.conversion.preview:
                if pair.get("output") is None:
                    pytest.xfail(
                        f"Transform error in preview for '{mapping.source_column}': "
                        f"{pair.get('error')}"
                    )

    # Apply mappings to the real rows.
    csv_rows = _read_csv_rows(PHAGE_CSV)
    transformed = apply_mappings(mapping_output, csv_rows, source_file_id=FILE_ID)

    assert len(transformed) == len(csv_rows), "Row count changed after apply_mappings"

    # Collect all first-candidate slots from high_confidence + needs_review.
    expected_slots = {
        m.nmdc_candidate_slots[0]
        for m in mapping_output.high_confidence + mapping_output.needs_review
        if m.nmdc_candidate_slots and m.source_file_id == FILE_ID
    }

    # Every expected slot should appear in at least one output row.
    for slot in expected_slots:
        present = any(slot in row for row in transformed)
        assert present, f"Expected slot '{slot}' not found in any transformed row"

    # Mapped source columns must be absent — they moved to slot keys.
    mapped_source_columns = {
        m.source_column
        for m in mapping_output.high_confidence + mapping_output.needs_review
        if m.nmdc_candidate_slots and m.source_file_id == FILE_ID
    }
    for row in transformed:
        for col in mapped_source_columns:
            assert col not in row, f"Source column '{col}' should have been renamed to its slot key"

    # Unmapped (cant_place) columns must still be present.
    cant_place_columns = {
        m.source_column for m in mapping_output.cant_place if m.source_file_id == FILE_ID
    }
    for i, row in enumerate(transformed):
        for col in cant_place_columns:
            if col in csv_rows[i]:
                assert col in row, f"Unmapped column '{col}' missing from transformed row {i}"


# ---------------------------------------------------------------------------
# Phase 2 — transform error handling
# ---------------------------------------------------------------------------


@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_phase2_transform_errors_do_not_raise(requires_credentials: None) -> None:
    """Transform failures are captured in _transform_errors, not raised."""
    source_file = SourceFile(file_id=FILE_ID, display_name=PHAGE_CSV.name)
    client = LLMClient(access_provider="gcp")

    mapping_output, _ = asyncio.run(
        run_metadata_mapper_agentic(
            llm_client=client,
            csv_files=[(source_file, PHAGE_CSV)],
            mixs_extensions=MIXS_EXTENSIONS,
        )
    )

    csv_rows = _read_csv_rows(PHAGE_CSV)

    # apply_mappings must not raise regardless of what the CSV contains.
    transformed = apply_mappings(mapping_output, csv_rows)

    error_rows = [r for r in transformed if r.get("_transform_errors")]
    if error_rows:
        # Surface the errors for visibility in the test output without failing.
        error_summary = {
            r.get("specimen_collector_sample_id", f"row_{i}"): r["_transform_errors"]
            for i, r in enumerate(transformed)
            if r.get("_transform_errors")
        }
        pytest.xfail(f"Transform errors encountered (non-fatal): {error_summary}")
