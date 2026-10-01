"""Shared helpers for the Metadata Mapper pipeline."""

import csv
from pathlib import Path

from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    MetadataMapperOutput,
    SourceFile,
)


def read_csv_files(
    csv_files: list[tuple[SourceFile, Path]],
    sample_n: int = 3,
) -> tuple[list[SourceFile], dict[str, dict[str, list[str]]]]:
    """Read column headers and up to `sample_n` sample values per column."""
    source_files: list[SourceFile] = []
    column_data: dict[str, dict[str, list[str]]] = {}
    for source_file, path in csv_files:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            samples: dict[str, list[str]] = {h: [] for h in headers}
            for row in reader:
                for h in headers:
                    if len(samples[h]) < sample_n:
                        val = (row.get(h) or "").strip()
                        if val and val not in samples[h]:
                            samples[h].append(val)
                if all(len(v) >= sample_n for v in samples.values()):
                    break
        source_files.append(source_file)
        column_data[source_file.file_id] = samples
    return source_files, column_data


def build_column_context(
    source_files: list[SourceFile],
    column_data: dict[str, dict[str, list[str]]],
    mixs_extensions: list[str],
) -> str:
    """
    Format uploaded file columns and user-selected extensions
    into a prompt-ready context block.
    """
    lines = ["The user has uploaded the following files with these columns:\n"]
    file_map = {f.file_id: f.display_name for f in source_files}
    for file_id, samples in column_data.items():
        display = file_map.get(file_id, file_id)
        col_parts = []
        for col, vals in samples.items():
            part = col
            if vals:
                part += f" [e.g. {' | '.join(vals)}]"
            col_parts.append(part)
        col_list = ", ".join(col_parts)
        lines.append(f"- {display} (id: {file_id}): {col_list}")
    if mixs_extensions:
        lines.append(f"\nUser-selected MIxS extensions: {', '.join(mixs_extensions)}")
    lines.append(
        "\nFor each column, identify the best-matching NMDC slot and MIxS extension. "
        "Classify your confidence as 'high', 'review', or 'cant_place'."
    )
    return "\n".join(lines)


def validate_output(raw: str | MetadataMapperOutput) -> MetadataMapperOutput:
    """Parse and validate the LLM response into a MetadataMapperOutput."""
    from pydantic import ValidationError

    if isinstance(raw, MetadataMapperOutput):
        return raw
    if isinstance(raw, str):
        try:
            return MetadataMapperOutput.model_validate_json(raw)
        except ValidationError as exc:
            raise ValueError(
                f"LLM response did not match MetadataMapperOutput schema: {exc}"
            ) from exc
    return MetadataMapperOutput()
