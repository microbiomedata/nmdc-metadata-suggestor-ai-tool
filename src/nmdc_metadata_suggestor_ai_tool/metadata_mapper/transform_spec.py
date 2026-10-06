"""Compile approved Metadata Mapper output into reusable linkml-map transforms.

The mapper agent decides, per column, which NMDC slot it maps to and how its values must be
converted. Once a reviewer approves that, this module turns it into a linkml-map
TransformationSpecification (a ``ReusableTransform``) that can be saved and run again,
deterministically, on any later file with the same shape.

Pieces:

- ``compile_transform``: MetadataMapperOutput → ReusableTransform
- ``run_transform``: apply a ReusableTransform to CSV rows through linkml-map
- ``match_transform`` / ``TransformLibrary``: find a saved transform for a new file's headers
- ``mapper_output_from_transform``: rebuild a MetadataMapperOutput from a saved transform,
  for the columns it covers, without calling the agent
- ``build_prior_transform_context``: prompt text that hands a saved transform to the agent
"""

import copy
import json
import keyword
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from linkml_map.transformer.object_transformer import (  # type: ignore[import-untyped]
    ObjectTransformer,
)
from linkml_runtime.utils.schemaview import SchemaView  # type: ignore[import-untyped]

from nmdc_metadata_suggestor_ai_tool.metadata_mapper.transform_functions import (
    TRANSFORM_FUNCTIONS,
    collect_transform_errors,
    is_blank,
)
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    ColumnMapping,
    MetadataMapperOutput,
    SourceFile,
)
from nmdc_metadata_suggestor_ai_tool.models.reusable_transform import ReusableTransform

SOURCE_CLASS = "Row"
TARGET_CLASS = "Biosample"

# Fraction of a saved transform's columns a new file must share before it counts as a match.
DEFAULT_MIN_OVERLAP = 0.8


def normalize_header(header: str) -> str:
    """Header form used for shape matching: case-folded, punctuation and spacing ignored.

    ``Sample_ID``, ``sample id`` and ``Sample-ID `` all normalize to ``sample id``.
    """
    return " ".join(re.sub(r"[\W_]+", " ", header).split()).casefold()


def header_to_slot_name(header: str, taken: set[str]) -> str:
    """Turn a CSV header into a unique, valid identifier for the induced source schema."""
    name = re.sub(r"\W+", "_", header.strip()).strip("_").lower() or "column"
    if name[0].isdigit() or keyword.iskeyword(name):
        name = f"col_{name}"
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name}_{n}", n + 1
    taken.add(candidate)
    return candidate


def build_column_slots(headers: list[str]) -> dict[str, str]:
    taken: set[str] = set()
    return {h: header_to_slot_name(h, taken) for h in headers}


def induce_source_schema(name: str, column_slots: dict[str, str]) -> dict[str, Any]:
    """A flat LinkML schema with one string slot per CSV column, for linkml-map to bind to."""
    slots = {
        slot: {"title": header} if header != slot else {} for header, slot in column_slots.items()
    }
    return {
        "id": f"https://w3id.org/nmdc/metadata-mapper/source/{name}",
        "name": f"{name}_source",
        "prefixes": {"linkml": "https://w3id.org/linkml/"},
        "imports": ["linkml:types"],
        "default_range": "string",
        "slots": slots,
        "classes": {SOURCE_CLASS: {"slots": list(slots)}},
    }


def slot_derivation(mapping: ColumnMapping, column_slots: dict[str, str]) -> dict[str, Any]:
    """Translate one ColumnMapping's conversion into a linkml-map slot derivation."""
    source = column_slots[mapping.source_column]
    conversion = mapping.conversion
    derivation: dict[str, Any] = {}
    if mapping.reason:
        derivation["description"] = mapping.reason

    if mapping.combine_columns:
        columns = [mapping.source_column, *mapping.combine_columns]
        values = ", ".join(f"{json.dumps(c)}: {column_slots[c]}" for c in columns)
        expression = conversion.expression if conversion else None
        derivation["expr"] = f"sandboxed_combined({{{values}}}, {expression!r})"
        return derivation

    kind = conversion.type.lower() if conversion else "none"
    expression = conversion.expression if conversion else None
    if kind == "none" or expression is None:
        derivation["populated_from"] = source
    elif kind == "enum_map":
        derivation["populated_from"] = source
        derivation["value_mappings"] = {
            str(k): {"value": str(v)} for k, v in json.loads(expression).items()
        }
    elif kind == "date_format":
        derivation["expr"] = f"iso_date({source}, {expression!r})"
    elif kind == "unit":
        derivation["expr"] = f"scale({source}, {expression!r})"
    elif kind == "split":
        derivation["expr"] = f"split_join({source}, {expression!r})"
    elif kind == "custom":
        derivation["expr"] = f"sandboxed({source}, {expression!r})"
    else:
        raise ValueError(f"No linkml-map translation for conversion type {kind!r}")
    return derivation


def compile_transform(
    mapper_output: MetadataMapperOutput,
    source_columns: list[str],
    name: str,
    source_file_id: str | None = None,
    mixs_extensions: list[str] | None = None,
    description: str = "",
) -> ReusableTransform:
    """Compile the approved mappings for one file into a ReusableTransform.

    Uses the same mappings ``apply_mappings`` would: high_confidence and needs_review entries
    that have a candidate slot, mapped to their first candidate. Pass ``source_file_id`` when
    the output covers more than one file.
    """
    column_slots = build_column_slots(source_columns)
    mappings = [
        m
        for m in mapper_output.high_confidence + mapper_output.needs_review
        if m.nmdc_candidate_slots
        and (source_file_id is None or m.source_file_id == source_file_id)
        and all(c in column_slots for c in [m.source_column, *m.combine_columns])
    ]
    derivations = {m.nmdc_candidate_slots[0]: slot_derivation(m, column_slots) for m in mappings}
    extensions = mixs_extensions or sorted({m.mixs_extension for m in mappings if m.mixs_extension})
    spec = {
        "id": f"https://w3id.org/nmdc/metadata-mapper/transform/{name}",
        "title": name,
        "description": description or None,
        "source_schema": {
            "name": f"{name}_source",
            "schema_uri": induce_source_schema(name, column_slots)["id"],
        },
        "class_derivations": {
            TARGET_CLASS: {"populated_from": SOURCE_CLASS, "slot_derivations": derivations}
        },
    }
    return ReusableTransform(
        name=name,
        description=description,
        source_columns=source_columns,
        column_slots=column_slots,
        mixs_extensions=extensions,
        mappings=mappings,
        spec={k: v for k, v in spec.items() if v is not None},
        created=date.today().isoformat(),
    )


def build_object_transformer(transform: ReusableTransform) -> ObjectTransformer:
    source_schema = induce_source_schema(transform.name, transform.column_slots)
    transformer = ObjectTransformer(extension_functions=dict(TRANSFORM_FUNCTIONS))
    transformer.source_schemaview = SchemaView(yaml.safe_dump(source_schema))
    # linkml-map normalizes the dict it is given in place; keep the saved spec untouched.
    transformer.create_transformer_specification(copy.deepcopy(transform.spec))
    return transformer


def run_transform(
    transform: ReusableTransform,
    csv_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run a ReusableTransform over CSV rows through linkml-map.

    Each output row holds only the mapped NMDC slots, plus ``_transform_errors`` when a cell
    failed to convert or an enum value had no mapping. Blank cells are left out, as
    ``apply_mappings`` does. Columns the transform does not know are ignored.
    """
    transformer = build_object_transformer(transform)
    derivations = transform.spec["class_derivations"][TARGET_CLASS]["slot_derivations"]
    results = []
    for row in csv_rows:
        source = {
            slot: (None if is_blank(row.get(header)) else str(row[header]))
            for header, slot in transform.column_slots.items()
        }
        with collect_transform_errors() as errors:
            mapped = transformer.map_object(source, source_type=SOURCE_CLASS)
        # value_mappings returns None on a miss rather than raising, so a source value the
        # enum map does not cover would vanish silently. Surface it, and keep the raw value.
        for slot, derivation in derivations.items():
            populated_from = derivation.get("populated_from")
            if derivation.get("value_mappings") and mapped.get(slot) is None:
                raw = source.get(populated_from)
                if raw is not None:
                    known = list(derivation["value_mappings"])
                    errors.append(
                        f"{slot}: enum_map: source value {raw!r} has no mapping; "
                        f"known keys: {known!r}"
                    )
                    mapped[slot] = raw
        out = {k: v for k, v in mapped.items() if v is not None}
        if errors:
            out["_transform_errors"] = errors
        results.append(out)
    return results


# ------------------------------------------------------------------
# Finding a saved transform for a new file
# ------------------------------------------------------------------


@dataclass
class TransformMatch:
    """How well a saved transform fits a new file's headers."""

    transform: ReusableTransform
    overlap: float  # share of the transform's columns present in the new file
    jaccard: float
    shared_columns: list[str] = field(default_factory=list)  # headers as the new file spells them
    missing_columns: list[str] = field(default_factory=list)  # in the transform, not the file
    new_columns: list[str] = field(default_factory=list)  # in the file, not the transform


def score_match(transform: ReusableTransform, headers: list[str]) -> TransformMatch:
    known = {normalize_header(h) for h in transform.source_columns}
    incoming = {normalize_header(h): h for h in headers}
    shared = known & incoming.keys()
    union = known | incoming.keys()
    return TransformMatch(
        transform=transform,
        overlap=len(shared) / len(known) if known else 0.0,
        jaccard=len(shared) / len(union) if union else 0.0,
        shared_columns=[incoming[h] for h in incoming if h in shared],
        missing_columns=[h for h in transform.source_columns if normalize_header(h) not in shared],
        new_columns=[incoming[h] for h in incoming if h not in known],
    )


def match_transform(
    headers: list[str],
    transforms: list[ReusableTransform],
    min_overlap: float = DEFAULT_MIN_OVERLAP,
) -> TransformMatch | None:
    """Return the best-fitting saved transform for these headers, or None below the bar."""
    matches = [score_match(t, headers) for t in transforms]
    matches = [m for m in matches if m.overlap >= min_overlap]
    if not matches:
        return None
    return max(matches, key=lambda m: (m.overlap, m.jaccard))


def rebase_transform(transform: ReusableTransform, headers: list[str]) -> ReusableTransform:
    """Re-key a saved transform to a new file's header spellings so it can run on that file.

    Matching ignores case and whitespace, so ``Sample ID`` and ``sample id`` match; the
    transform's own column names must still be swapped for the file's before running.
    """
    by_normal = {normalize_header(h): h for h in headers}
    renamed = {
        by_normal.get(normalize_header(h), h): slot for h, slot in transform.column_slots.items()
    }
    mappings = []
    for m in transform.mappings:
        m = m.model_copy(deep=True)
        m.source_column = by_normal.get(normalize_header(m.source_column), m.source_column)
        m.combine_columns = [by_normal.get(normalize_header(c), c) for c in m.combine_columns]
        mappings.append(m)
    return transform.model_copy(
        update={
            "column_slots": renamed,
            "source_columns": [
                by_normal.get(normalize_header(h), h) for h in transform.source_columns
            ],
            "mappings": mappings,
        }
    )


class TransformLibrary:
    """A directory of saved transforms, one ``<name>.yaml`` per transform."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def path_for(self, name: str) -> Path:
        return self.directory / f"{name}.yaml"

    def save(self, transform: ReusableTransform) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(transform.name)
        path.write_text(yaml.safe_dump(transform.model_dump(mode="json"), sort_keys=False))
        return path

    def load(self, name: str) -> ReusableTransform:
        return ReusableTransform.model_validate(yaml.safe_load(self.path_for(name).read_text()))

    def load_all(self) -> list[ReusableTransform]:
        if not self.directory.is_dir():
            return []
        return [
            ReusableTransform.model_validate(yaml.safe_load(p.read_text()))
            for p in sorted(self.directory.glob("*.yaml"))
        ]

    def find_match(
        self, headers: list[str], min_overlap: float = DEFAULT_MIN_OVERLAP
    ) -> TransformMatch | None:
        return match_transform(headers, self.load_all(), min_overlap)


# ------------------------------------------------------------------
# Handing a saved transform back to the mapper
# ------------------------------------------------------------------


def mapper_output_from_transform(
    transform: ReusableTransform,
    source_file: SourceFile,
    headers: list[str],
) -> tuple[MetadataMapperOutput, list[str]]:
    """Rebuild mapper output from a saved transform for the columns it covers. No LLM call.

    Returns the output and the headers the transform does not cover. Those still need the
    agent (or a person). Mappings whose columns are all present keep their saved confidence.
    """
    rebased = rebase_transform(transform, headers)
    present = set(headers)
    output = MetadataMapperOutput(source_files=[source_file])
    covered: set[str] = set()
    for m in rebased.mappings:
        columns = [m.source_column, *m.combine_columns]
        if not all(c in present for c in columns):
            continue
        m = m.model_copy(update={"source_file_id": source_file.file_id})
        (output.high_confidence if m.confidence == "high" else output.needs_review).append(m)
        covered.update(columns)
    return output, [h for h in headers if h not in covered]


def build_prior_transform_context(match: TransformMatch) -> str:
    """Prompt text telling the mapper agent about a saved transform that fits this file."""
    t = match.transform
    lines = [
        f"\nA saved transform, '{t.name}', matches this file's columns "
        f"({match.overlap:.0%} of its columns are present).",
    ]
    if t.description:
        lines.append(f"It came from: {t.description}")
    if t.mixs_extensions:
        lines.append(f"It was built for MIxS extensions: {', '.join(t.mixs_extensions)}")
    lines.append(
        "Its approved mappings follow, one JSON object per line. Reuse a mapping as-is when its "
        "column is present and the sample values above still fit its conversion (same date "
        "format, same units, enum keys that cover the values). Only look up schema details "
        "for columns that are not covered, or where the values no longer fit."
    )
    for m in t.mappings:
        entry: dict[str, Any] = {
            "source_column": m.source_column,
            "slot": m.nmdc_candidate_slots[0] if m.nmdc_candidate_slots else None,
            "mixs_extension": m.mixs_extension,
            "confidence": m.confidence,
        }
        if m.combine_columns:
            entry["combine_columns"] = m.combine_columns
        if m.conversion and m.conversion.type != "none":
            entry["conversion"] = {"type": m.conversion.type, "expression": m.conversion.expression}
        lines.append(json.dumps(entry))
    if match.new_columns:
        lines.append(f"Columns the saved transform does not cover: {', '.join(match.new_columns)}")
    return "\n".join(lines)
