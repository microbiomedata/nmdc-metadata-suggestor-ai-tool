"""Score the sample-value-filling skill's output against real biosample values.

Offline and deterministic: it reads the captured artifacts in this directory
(no live NMDC API calls) and checks the skill's output three ways:

1. It validates against the real ``LLMOutput`` model.
2. Every emitted value is checked against its SedimentInterface slot pattern.
3. Emitted values are compared to ground truth captured from the NMDC API, and
   the fields the skill declined to fill are listed (the restraint check).
4. Every required slot a row is missing is either filled or flagged as required
   (an entry with an empty value and the fixed required-slot reason).

Run with: ``uv run python .claude/skills/sample-value-filling/eval/score.py``
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder, get_schema_view

HERE = Path(__file__).resolve().parent
INTERFACE = "SedimentInterface"
# Slots that must not be filled for this submission: the evidence names no coordinates,
# elevations, depths, times, or processing. Required ones among them are flagged with an
# empty value instead; non-required ones should be absent from the output entirely.
EXPECTED_UNFILLED = ("lat_lon", "elev", "depth", "collection_time", "samp_mat_process")
REQUIRED_REASON = "Required slot; no value found in the available evidence."


def load_json(name: str) -> dict | list:
    return json.loads((HERE / name).read_text())


def slot_patterns() -> dict[str, str]:
    sv = get_schema_view()
    return {s.name: s.pattern for s in sv.class_induced_slots(INTERFACE) if s.pattern}


def main() -> None:
    output = LLMOutput.model_validate(load_json("llm_output.json"))
    truth = {row["samp_name"]: row for row in load_json("ground_truth.json")}
    submission = load_json("submission.json")
    rows = submission["metadata_submission"]["sampleData"]["sediment_data"]
    patterns = slot_patterns()

    print(f"Pydantic validation: OK ({len(output.metadata_fields)} entries)")

    pattern_failures = [
        (f.id, f.field_name, f.value)
        for f in output.metadata_fields
        if f.value != ""
        and f.field_name in patterns
        and not re.match(patterns[f.field_name], str(f.value))
    ]
    print(f"Slot-pattern failures: {pattern_failures or 'none'}")

    print("\ngeo_loc_name (emitted vs real):")
    for f in output.metadata_fields:
        if f.field_name != "geo_loc_name":
            continue
        real = truth[f.id]["geo_loc_name"]
        emit_parts = [p.strip() for p in re.split("[:,]", f.value)]
        real_parts = [p.strip() for p in re.split("[:,]", real)]
        country_ok = emit_parts[0] == real_parts[0]
        region_ok = len(emit_parts) > 1 and len(real_parts) > 1 and emit_parts[1] == real_parts[1]
        print(
            f"  {f.id:30} emit={f.value!r:42} real={real!r:36} "
            f"country={'OK' if country_ok else 'X'} region={'OK' if region_ok else 'X'}"
        )

    print("\ncollection_date (emitted vs real):")
    for f in output.metadata_fields:
        if f.field_name != "collection_date":
            continue
        real = str(truth[f.id]["collection_date"])
        year_ok = real[:4] == str(f.value)
        print(f"  {f.id:30} emit={f.value!r:8} real={real!r:14} year={'OK' if year_ok else 'X'}")

    filled = {f.field_name for f in output.metadata_fields if f.value != ""}
    flagged = {f.field_name for f in output.metadata_fields if f.value == ""}
    print("\nfields filled:", sorted(filled))
    print("fields flagged with an empty value:", sorted(flagged))
    print("correctly left unfilled (no evidence):", sorted(set(EXPECTED_UNFILLED) - filled))
    leaked = sorted(set(EXPECTED_UNFILLED) & filled)
    if leaked:
        print("WARNING: filled fields that have no supporting evidence:", leaked)

    print("\nrequired-slot coverage (every missing required slot filled or flagged):")
    builder = SchemaContextBuilder()
    by_cell = {(f.id, f.field_name): f for f in output.metadata_fields}
    gaps: list[tuple[str, str]] = []
    bad_reasons: list[tuple[str, str]] = []
    for row in rows:
        for slot in builder.missing_required_slots(INTERFACE, row):
            entry = by_cell.get((row["samp_name"], slot))
            if entry is None:
                gaps.append((row["samp_name"], slot))
            elif entry.value == "" and entry.reason != REQUIRED_REASON:
                bad_reasons.append((row["samp_name"], slot))
    print(f"  silently dropped: {gaps or 'none'}")
    print(f"  flagged with a non-standard reason: {bad_reasons or 'none'}")


if __name__ == "__main__":
    main()
