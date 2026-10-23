"""Score the sample-value-filling skill's output against real biosample values.

Offline and deterministic: it reads the captured artifacts in this directory
(no live NMDC API calls) and checks the skill's output three ways:

1. It validates against the real ``LLMOutput`` model.
2. Every emitted value is checked against its SedimentInterface slot pattern.
3. Emitted values are compared to ground truth captured from the NMDC API, and
   the fields the skill declined to fill are listed (the restraint check).

Run with: ``uv run python .claude/skills/sample-value-filling/eval/score.py``
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput
from nmdc_metadata_suggestor_ai_tool.schema_context import get_schema_view

HERE = Path(__file__).resolve().parent
INTERFACE = "SedimentInterface"
# Slots whose absence from the output is the correct answer for this submission:
# the evidence names no coordinates, elevations, depths, times, or processing.
EXPECTED_OMISSIONS = ("lat_lon", "elev", "depth", "collection_time", "samp_mat_process")


def load_json(name: str) -> dict | list:
    return json.loads((HERE / name).read_text())


def slot_patterns() -> dict[str, str]:
    sv = get_schema_view()
    return {s.name: s.pattern for s in sv.class_induced_slots(INTERFACE) if s.pattern}


def main() -> None:
    output = LLMOutput.model_validate(load_json("llm_output.json"))
    truth = {row["samp_name"]: row for row in load_json("ground_truth.json")}
    patterns = slot_patterns()

    print(f"Pydantic validation: OK ({len(output.metadata_fields)} entries)")

    pattern_failures = [
        (f.id, f.field_name, f.value)
        for f in output.metadata_fields
        if f.field_name in patterns and not re.match(patterns[f.field_name], str(f.value))
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

    emitted = {f.field_name for f in output.metadata_fields}
    print("\nfields emitted:", sorted(emitted))
    print("correctly omitted (no evidence):", sorted(set(EXPECTED_OMISSIONS) - emitted))
    leaked = sorted(set(EXPECTED_OMISSIONS) & emitted)
    if leaked:
        print("WARNING: emitted fields that have no supporting evidence:", leaked)


if __name__ == "__main__":
    main()
