"""Compile approved Metadata Mapper output into reusable linkml-map transforms.

The mapper agent decides, per column, which NMDC slot it maps to and how its values must be
converted. Once a reviewer approves that, this module turns it into ordinary linkml-map files
(a ``ReusableTransform``) that can be saved and run again, deterministically, on any later file
with the same shape:

- ``source_schema.yaml``: a LinkML schema induced from the CSV headers. One class, ``Row``,
  with one string slot per column, named exactly as the header is spelled.
- ``transform.yaml``: a linkml-map TransformationSpecification. One class derivation per NMDC
  submission schema interface the mappings use (``WaterInterface``, ``SoilInterface``, ...),
  with ``target_schema`` pointing at the nmdc-submission-schema package.
- ``mapper.yaml``: the approved mappings, with confidence and reasons.

The pair runs with the linkml-map CLI as well as from Python (``run_transform``)::

    linkml-map map-data -T transform.yaml -s source_schema.yaml \\
        --functions <package>/metadata_mapper/transform_functions.py Row.csv

(the CLI takes the source class from the input file's stem, so the CSV must be named
``Row.csv``).

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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from importlib.metadata import version
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
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder

SOURCE_CLASS = "Row"

# Hidden working slots that copy a raw column into an identifier an expression can use.
HIDDEN_PREFIX = "src_"

TARGET_SCHEMA_NAME = "nmdc_submission_schema"
TARGET_SCHEMA_FILE = "nmdc_submission_schema/schema/nmdc_submission_schema.yaml"

# Fraction of a saved transform's columns a new file must share before it counts as a match.
DEFAULT_MIN_OVERLAP = 0.8

SOURCE_SCHEMA_FILE = "source_schema.yaml"
SPEC_FILE = "transform.yaml"
MAPPER_FILE = "mapper.yaml"


def normalize_header(header: str) -> str:
    """Header form used for shape matching: case-folded, punctuation and spacing ignored.

    ``Sample_ID``, ``sample id`` and ``Sample-ID `` all normalize to ``sample id``.
    """
    return " ".join(re.sub(r"[\W_]+", " ", header).split()).casefold()


def header_to_identifier(header: str, taken: set[str]) -> str:
    """A unique, valid identifier for a header, for use in linkml-map expressions."""
    name = re.sub(r"\W+", "_", header.strip()).strip("_").lower() or "column"
    if name[0].isdigit() or keyword.iskeyword(name):
        name = f"col_{name}"
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name}_{n}", n + 1
    taken.add(candidate)
    return candidate


def hidden_slot_names(headers: list[str]) -> dict[str, str]:
    """Header → name of the hidden working slot that carries it into expressions."""
    taken: set[str] = set()
    return {h: HIDDEN_PREFIX + header_to_identifier(h, taken) for h in headers}


def induce_source_schema(name: str, headers: list[str]) -> dict[str, Any]:
    """A flat LinkML schema with one string slot per CSV column, named as the header is.

    Slot names keep the header's exact spelling (spaces, parentheses and all) so the linkml-map
    CLI can read the raw CSV. Expressions cannot use such names, which is what the hidden
    working slots in the spec are for.
    """
    return {
        "id": f"https://w3id.org/nmdc/metadata-mapper/source/{name}",
        "name": f"{name}_source",
        "description": "Induced from CSV headers by the NMDC Metadata Mapper.",
        "prefixes": {"linkml": "https://w3id.org/linkml/"},
        "imports": ["linkml:types"],
        "default_range": "string",
        "slots": {h: {} for h in headers},
        "classes": {SOURCE_CLASS: {"slots": list(headers)}},
    }


def target_schema_reference(builder: SchemaContextBuilder) -> dict[str, Any]:
    schema = builder.sv.schema
    return {
        "name": TARGET_SCHEMA_NAME,
        "version": schema.version or version("nmdc-submission-schema"),
        "schema_uri": schema.id,
        "source_file": TARGET_SCHEMA_FILE,
    }


def interface_for(extension: str | None, interfaces: dict[str, str]) -> str:
    """Submission schema interface class for a mapper ``mixs_extension`` (``Water`` →
    ``WaterInterface``)."""
    ext = (extension or "").strip()
    key = ext if ext.casefold().endswith("interface") else f"{ext}Interface"
    key = key.replace("-", "").replace("_", "").replace(" ", "")
    return interfaces.get(key.casefold(), key)


def columns_of(mapping: ColumnMapping) -> list[str]:
    return [mapping.source_column, *mapping.combine_columns]


def needs_expression(mapping: ColumnMapping) -> bool:
    if mapping.combine_columns:
        return True
    kind = mapping.conversion.type.lower() if mapping.conversion else "none"
    return (
        kind not in ("none", "enum_map")
        and mapping.conversion is not None
        and bool(mapping.conversion.expression)
    )


def slot_derivation(mapping: ColumnMapping, hidden: dict[str, str]) -> dict[str, Any]:
    """Translate one ColumnMapping's conversion into a linkml-map slot derivation."""
    conversion = mapping.conversion
    derivation: dict[str, Any] = {}
    if mapping.reason:
        derivation["description"] = mapping.reason

    if mapping.combine_columns:
        values = ", ".join(f"{json.dumps(c)}: slot({hidden[c]!r})" for c in columns_of(mapping))
        expression = conversion.expression if conversion else None
        derivation["expr"] = f"sandboxed_combined({{{values}}}, {expression!r})"
        return derivation

    kind = conversion.type.lower() if conversion else "none"
    expression = conversion.expression if conversion else None
    if kind == "none" or expression is None:
        derivation["populated_from"] = mapping.source_column
        derivation["missing_values"] = [""]
        return derivation
    if kind == "enum_map":
        derivation["populated_from"] = mapping.source_column
        derivation["missing_values"] = [""]
        derivation["value_mappings"] = {
            str(k): {"value": str(v)} for k, v in json.loads(expression).items()
        }
        return derivation

    helper = {
        "date_format": "iso_date",
        "unit": "scale",
        "split": "split_join",
        "custom": "sandboxed",
    }.get(kind)
    if helper is None:
        raise ValueError(f"No linkml-map translation for conversion type {kind!r}")
    derivation["expr"] = f"{helper}(slot({hidden[mapping.source_column]!r}), {expression!r})"
    return derivation


def build_transform(
    name: str,
    mappings: list[ColumnMapping],
    source_columns: list[str],
    mixs_extensions: list[str] | None = None,
    description: str = "",
    schema_builder: SchemaContextBuilder | None = None,
) -> ReusableTransform:
    """Build the linkml-map source schema and spec for a list of approved mappings."""
    for m in mappings:
        dotted = [c for c in columns_of(m) if "." in c]
        if dotted:
            # linkml-map reads a dot in populated_from as table.column (a join).
            raise ValueError(f"Column names containing '.' are not supported yet: {dotted}")

    builder = schema_builder or SchemaContextBuilder()
    interfaces = {i.casefold(): i for i in builder.list_interfaces()}
    used = [c for m in mappings if needs_expression(m) for c in columns_of(m)]
    hidden = hidden_slot_names(source_columns)

    class_derivations: dict[str, Any] = {}
    for m in mappings:
        target = interface_for(m.mixs_extension, interfaces)
        derivations = class_derivations.setdefault(
            target, {"populated_from": SOURCE_CLASS, "slot_derivations": {}}
        )["slot_derivations"]
        # Hidden copies first: derivations run in declaration order, and slot() reads
        # only what has already been derived.
        for column in columns_of(m):
            if column in used and hidden[column] not in derivations:
                derivations[hidden[column]] = {
                    "populated_from": column,
                    "missing_values": [""],
                    "hide": True,
                }
        derivations[m.nmdc_candidate_slots[0]] = slot_derivation(m, hidden)

    extensions = mixs_extensions or sorted({m.mixs_extension for m in mappings if m.mixs_extension})
    spec: dict[str, Any] = {
        "id": f"https://w3id.org/nmdc/metadata-mapper/transform/{name}",
        "title": name,
    }
    if description:
        spec["description"] = description
    spec["source_schema"] = {
        "name": f"{name}_source",
        "schema_uri": f"https://w3id.org/nmdc/metadata-mapper/source/{name}",
        "source_file": SOURCE_SCHEMA_FILE,
    }
    spec["target_schema"] = target_schema_reference(builder)
    spec["class_derivations"] = class_derivations
    return ReusableTransform(
        name=name,
        description=description,
        source_columns=source_columns,
        mixs_extensions=extensions,
        mappings=mappings,
        source_schema=induce_source_schema(name, source_columns),
        spec=spec,
        created=date.today().isoformat(),
    )


def compile_transform(
    mapper_output: MetadataMapperOutput,
    source_columns: list[str],
    name: str,
    source_file_id: str | None = None,
    mixs_extensions: list[str] | None = None,
    description: str = "",
    schema_builder: SchemaContextBuilder | None = None,
) -> ReusableTransform:
    """Compile the approved mappings for one file into a ReusableTransform.

    Uses the same mappings ``apply_mappings`` would: high_confidence and needs_review entries
    that have a candidate slot, mapped to their first candidate. Pass ``source_file_id`` when
    the output covers more than one file.
    """
    present = set(source_columns)
    mappings = [
        m
        for m in mapper_output.high_confidence + mapper_output.needs_review
        if m.nmdc_candidate_slots
        and (source_file_id is None or m.source_file_id == source_file_id)
        and all(c in present for c in columns_of(m))
    ]
    return build_transform(
        name, mappings, source_columns, mixs_extensions, description, schema_builder
    )


def build_object_transformer(transform: ReusableTransform) -> ObjectTransformer:
    transformer = ObjectTransformer(extension_functions=dict(TRANSFORM_FUNCTIONS))
    transformer.source_schemaview = SchemaView(yaml.safe_dump(transform.source_schema))
    # linkml-map normalizes the dict it is given in place; keep the saved spec untouched.
    transformer.create_transformer_specification(copy.deepcopy(transform.spec))
    return transformer


def run_transform(
    transform: ReusableTransform,
    csv_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run a ReusableTransform over CSV rows through linkml-map.

    Rows must be keyed by ``transform.source_columns`` (see ``rebase_transform`` for a file
    that spells them differently). Each output row merges every interface's mapped slots, plus
    ``_transform_errors`` when a cell failed to convert or an enum value had no mapping. Blank
    cells are left out, as ``apply_mappings`` does.
    """
    transformer = build_object_transformer(transform)
    class_derivations = transformer.specification.class_derivations
    results = []
    for row in csv_rows:
        source = {
            h: (None if is_blank(row.get(h)) else str(row[h])) for h in transform.source_columns
        }
        mapped: dict[str, Any] = {}
        with collect_transform_errors() as errors:
            for class_derivation in class_derivations:
                mapped.update(
                    transformer.map_object(
                        source, source_type=SOURCE_CLASS, class_derivation=class_derivation
                    )
                )
        # value_mappings returns None on a miss rather than raising, so a source value the
        # enum map does not cover would vanish silently. Surface it, and keep the raw value.
        for derivations in transform.spec["class_derivations"].values():
            for slot, derivation in derivations["slot_derivations"].items():
                if derivation.get("value_mappings") and mapped.get(slot) is None:
                    raw = source.get(derivation["populated_from"])
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


def rebase_transform(
    transform: ReusableTransform,
    headers: list[str],
    schema_builder: SchemaContextBuilder | None = None,
) -> ReusableTransform:
    """Rebuild a saved transform against a new file's header spellings so it can run on it.

    Matching ignores case and punctuation, so ``Sample ID`` and ``sample_id`` match, but the
    schema and spec name columns exactly, so they are rebuilt from the re-keyed mappings.
    """
    by_normal = {normalize_header(h): h for h in headers}

    def respell(column: str) -> str:
        return by_normal.get(normalize_header(column), column)

    mappings = []
    for m in transform.mappings:
        m = m.model_copy(deep=True)
        if m.combine_columns and m.conversion and m.conversion.expression:
            # Combine expressions index ``values`` by column name, so respell those too.
            m.conversion.expression = respell_values_keys(m.conversion.expression, respell)
        m.source_column = respell(m.source_column)
        m.combine_columns = [respell(c) for c in m.combine_columns]
        mappings.append(m)
    rebuilt = build_transform(
        transform.name,
        mappings,
        [respell(h) for h in transform.source_columns],
        transform.mixs_extensions,
        transform.description,
        schema_builder,
    )
    return rebuilt.model_copy(update={"created": transform.created})


def respell_values_keys(expression: str, respell: Callable[[str], str]) -> str:
    """Rewrite ``values['Col']`` / ``values["Col"]`` in a combine expression via ``respell``."""
    return re.sub(
        r"""values\[(['"])(.*?)\1\]""",
        lambda m: f"values[{m.group(1)}{respell(m.group(2))}{m.group(1)}]",
        expression,
    )


class TransformLibrary:
    """A directory of saved transforms, one folder of linkml-map files per transform::

    <directory>/<name>/source_schema.yaml
    <directory>/<name>/transform.yaml
    <directory>/<name>/mapper.yaml
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def path_for(self, name: str) -> Path:
        return self.directory / name

    def save(self, transform: ReusableTransform) -> Path:
        return write_transform_files(transform, self.path_for(transform.name))

    def load(self, name: str) -> ReusableTransform:
        return read_transform_files(self.path_for(name))

    def load_all(self) -> list[ReusableTransform]:
        if not self.directory.is_dir():
            return []
        return [
            read_transform_files(p.parent) for p in sorted(self.directory.glob(f"*/{SPEC_FILE}"))
        ]

    def find_match(
        self, headers: list[str], min_overlap: float = DEFAULT_MIN_OVERLAP
    ) -> TransformMatch | None:
        return match_transform(headers, self.load_all(), min_overlap)


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def write_transform_files(transform: ReusableTransform, directory: Path) -> Path:
    """Write a transform as source_schema.yaml, transform.yaml and mapper.yaml."""
    directory.mkdir(parents=True, exist_ok=True)
    data = transform.model_dump(mode="json")
    (directory / SOURCE_SCHEMA_FILE).write_text(dump_yaml(data.pop("source_schema")))
    (directory / SPEC_FILE).write_text(dump_yaml(data.pop("spec")))
    (directory / MAPPER_FILE).write_text(dump_yaml(data))
    return directory


def read_transform_files(directory: Path) -> ReusableTransform:
    data = yaml.safe_load((directory / MAPPER_FILE).read_text())
    data["source_schema"] = yaml.safe_load((directory / SOURCE_SCHEMA_FILE).read_text())
    data["spec"] = yaml.safe_load((directory / SPEC_FILE).read_text())
    return ReusableTransform.model_validate(data)


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
