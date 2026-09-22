"""Public API for applying column mappings to CSV row data."""

import logging
from collections.abc import Iterator
from typing import Any

from nmdc_metadata_suggestor_ai_tool.metadata_mapper.transformer import (
    TransformError,
    ValueTransformer,
)
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    ColumnMapping,
    MetadataMapperOutput,
)

logger = logging.getLogger(__name__)

_transformer = ValueTransformer()


def apply_mappings(
    mapping_output: MetadataMapperOutput,
    csv_rows: list[dict[str, Any]],
    source_file_id: str | None = None,
) -> list[dict[str, Any]]:
    """Apply approved column mappings to a list of CSV row dicts.

    Each output row contains:
    - All original key/value pairs (unchanged)
    - New keys for each mapped slot (``<slot_name>``) with the transformed value
    - A ``_transform_errors`` list of any per-cell errors that did not halt processing

    Parameters
    ----------
    mapping_output:
        The MetadataMapperOutput returned by the mapper agent (post-user-approval).
    csv_rows:
        Raw rows from the uploaded CSV, each as a dict keyed by column name.
    source_file_id:
        When provided, only mappings whose source_file_id matches are applied.
        Useful when multiple files were mapped together.

    Returns
    -------
    Transformed row dicts with new slot keys added alongside original columns.
    """
    active_mappings = _collect_mappings(mapping_output, source_file_id)
    return [_apply_row(row, active_mappings) for row in csv_rows]


def build_conversion_previews(
    mapping_output: MetadataMapperOutput,
    csv_rows: list[dict[str, Any]],
    n: int = 3,
) -> MetadataMapperOutput:
    """Populate ValueConversion.preview from real CSV rows for every mapped column.

    For single-column mappings, samples up to ``n`` non-empty values and runs
    them through ValueTransformer. For combine mappings, samples rows where all
    combine columns are present. Errors are recorded as
    {"input": ..., "output": null, "error": msg}.

    Call this right after Phase 1 (run_metadata_mapper_agentic) and before
    storing the job or sending the output to the UI.
    """
    for mapping in _iter_all_mappings(mapping_output):
        if not mapping.conversion or mapping.conversion.type == "none":
            continue

        pairs: list[dict[str, Any]] = []

        if mapping.combine_columns:
            all_cols = [mapping.source_column, *mapping.combine_columns]
            sample_rows = [
                row for row in csv_rows if all(row.get(c) not in ("", None) for c in all_cols)
            ][:n]
            for row in sample_rows:
                values = {c: str(row[c]) for c in all_cols}
                try:
                    out = _transformer.transform_combined(values, mapping.conversion)
                    pairs.append({"input": values, "output": out})
                except TransformError as exc:
                    pairs.append({"input": values, "output": None, "error": str(exc)})
        else:
            samples = [
                str(row[mapping.source_column])
                for row in csv_rows
                if mapping.source_column in row and row[mapping.source_column] not in ("", None)
            ][:n]
            for raw in samples:
                try:
                    out = _transformer.transform(raw, mapping.conversion)
                    pairs.append({"input": raw, "output": out})
                except TransformError as exc:
                    pairs.append({"input": raw, "output": None, "error": str(exc)})

        mapping.conversion.preview = pairs

    return mapping_output


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _collect_mappings(
    mapping_output: MetadataMapperOutput,
    source_file_id: str | None,
) -> list[ColumnMapping]:
    mappings = []
    for m in _iter_all_mappings(mapping_output):
        if source_file_id and m.source_file_id != source_file_id:
            continue
        if not m.nmdc_candidate_slots:
            continue
        mappings.append(m)
    return mappings


def _iter_all_mappings(mapping_output: MetadataMapperOutput) -> Iterator[ColumnMapping]:
    yield from mapping_output.high_confidence
    yield from mapping_output.needs_review


def _apply_row(
    row: dict[str, Any],
    mappings: list[ColumnMapping],
) -> dict[str, Any]:
    # All source columns that are consumed by a mapping (single or combine).
    # These are dropped from the output — their values move to the slot key.
    consumed: set[str] = set()
    for m in mappings:
        if m.nmdc_candidate_slots:
            consumed.add(m.source_column)
            consumed.update(m.combine_columns)

    # Start with columns that have no mapping (cant_place or not in this file).
    out: dict[str, Any] = {k: v for k, v in row.items() if k not in consumed}
    errors: list[str] = []

    for mapping in mappings:
        slot = mapping.nmdc_candidate_slots[0]

        if mapping.combine_columns:
            all_cols = [mapping.source_column, *mapping.combine_columns]
            absent = [c for c in all_cols if c not in row]
            empty = [c for c in all_cols if c in row and row.get(c) in (None, "")]
            if absent:
                errors.append(f"{slot}: combine columns not in CSV: {absent}")
                continue
            if empty:
                errors.append(f"{slot}: combine columns have no value in this row: {empty}")
                continue
            values = {c: str(row[c]) for c in all_cols}
            if mapping.conversion:
                try:
                    out[slot] = _transformer.transform_combined(values, mapping.conversion)
                except TransformError as exc:
                    errors.append(f"{slot}: {exc}")
                    out[slot] = str(values)
            else:
                out[slot] = str(values)
            continue

        raw = row.get(mapping.source_column)
        if raw is None:
            continue
        raw_str = str(raw)

        if mapping.conversion and mapping.conversion.type != "none":
            try:
                out[slot] = _transformer.transform(raw_str, mapping.conversion)
            except TransformError as exc:
                errors.append(f"{mapping.source_column}: {exc}")
                # On error keep the raw value under the slot key so nothing is lost.
                out[slot] = raw_str
        else:
            out[slot] = raw_str

    if errors:
        out.setdefault("_transform_errors", [])
        out["_transform_errors"].extend(errors)

    return out
