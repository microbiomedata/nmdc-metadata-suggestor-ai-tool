"""Compare Metadata Mapper runs: which slot each column went to, and what values came out.

Used by the transform-reuse eval (evaluation/transform_reuse.py) to compare a mapper run
that was handed a saved linkml-map transform against one that started cold. There is no
gold mapping for these files, so agreement is measured against the cold run, not truth.
"""

from collections import Counter
from typing import Any

from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    ColumnMapping,
    MetadataMapperOutput,
)

# ResultMessage fields the suggestor copies onto MetadataMapperOutput.run_health.
HEALTH_FIELDS = (
    "num_turns",
    "duration_ms",
    "total_cost_usd",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


def all_mappings(output: MetadataMapperOutput) -> list[ColumnMapping]:
    return output.high_confidence + output.needs_review + output.cant_place


def column_summary(output: MetadataMapperOutput) -> dict[str, dict[str, Any]]:
    """Per source column: top slot (None if unplaced), confidence, and conversion."""
    summary = {}
    for m in all_mappings(output):
        conversion = m.conversion
        summary[m.source_column] = {
            "slot": m.nmdc_candidate_slots[0] if m.nmdc_candidate_slots else None,
            "confidence": m.confidence,
            "conversion_type": conversion.type if conversion else "none",
            "expression": conversion.expression if conversion else None,
            "combine_columns": list(m.combine_columns),
        }
    return summary


def confidence_counts(output: MetadataMapperOutput) -> dict[str, int]:
    return {
        "high": len(output.high_confidence),
        "review": len(output.needs_review),
        "cant_place": len(output.cant_place),
    }


def compare_mappings(
    reference: MetadataMapperOutput, candidate: MetadataMapperOutput
) -> dict[str, Any]:
    """Column-level agreement of ``candidate`` with ``reference``.

    ``slot_agreement`` counts columns both runs addressed where the top slot is the same
    (both unplaced counts as agreement). ``conversion_agreement`` is over columns where the
    slots agree and at least one run converts the value: same type and same expression.
    """
    ref, cand = column_summary(reference), column_summary(candidate)
    shared = sorted(ref.keys() & cand.keys())
    same_slot = [c for c in shared if ref[c]["slot"] == cand[c]["slot"]]
    converted = [
        c
        for c in same_slot
        if ref[c]["conversion_type"] != "none" or cand[c]["conversion_type"] != "none"
    ]
    same_conversion = [
        c
        for c in converted
        if (ref[c]["conversion_type"], ref[c]["expression"])
        == (cand[c]["conversion_type"], cand[c]["expression"])
    ]
    return {
        "columns_compared": len(shared),
        "only_in_reference": sorted(ref.keys() - cand.keys()),
        "only_in_candidate": sorted(cand.keys() - ref.keys()),
        "slot_agreement": ratio(len(same_slot), len(shared)),
        "conversion_agreement": ratio(len(same_conversion), len(converted)),
        "slot_disagreements": [
            {"column": c, "reference": ref[c]["slot"], "candidate": cand[c]["slot"]}
            for c in shared
            if c not in same_slot
        ],
        "conversion_disagreements": [
            {
                "column": c,
                "reference": [ref[c]["conversion_type"], ref[c]["expression"]],
                "candidate": [cand[c]["conversion_type"], cand[c]["expression"]],
            }
            for c in converted
            if c not in same_conversion
        ],
    }


def compare_values(
    reference_rows: list[dict[str, Any]], candidate_rows: list[dict[str, Any]], slots: set[str]
) -> dict[str, Any]:
    """Cell-level agreement of transformed rows on the given slots.

    Rows are paired by position (same input file). A cell counts when either side has a value.
    """
    agree = total = 0
    differing: Counter[str] = Counter()
    for ref, cand in zip(reference_rows, candidate_rows, strict=True):
        for slot in slots:
            a, b = ref.get(slot), cand.get(slot)
            if a is None and b is None:
                continue
            total += 1
            if a == b:
                agree += 1
            else:
                differing[slot] += 1
    return {
        "cells_compared": total,
        "value_agreement": ratio(agree, total),
        "slots_with_differences": dict(differing.most_common()),
    }


def count_transform_errors(rows: list[dict[str, Any]]) -> int:
    return sum(len(row.get("_transform_errors", [])) for row in rows)


def mapped_slots(output: MetadataMapperOutput) -> set[str]:
    """NMDC slots that ``apply_mappings`` would write for this output."""
    return {
        m.nmdc_candidate_slots[0]
        for m in output.high_confidence + output.needs_review
        if m.nmdc_candidate_slots
    }


def run_cost(output: MetadataMapperOutput) -> dict[str, Any]:
    health = getattr(output, "run_health", {}) or {}
    return {field: health.get(field) for field in HEALTH_FIELDS}


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None
