"""Build the worked linkml-map examples in examples/linkml-map/ (issue 177 prototype).

Each example folder holds an input file and a hand-written set of approved mappings:

    Row.csv                  the input (named for the source class, as the linkml-map CLI wants)
    approved_mappings.yaml   what a reviewer approved: description, extensions, ColumnMappings

and this module generates the rest, exactly as ``TransformLibrary.save`` would:

    source_schema.yaml       LinkML schema induced from Row.csv's headers
    transform.yaml           linkml-map TransformationSpecification
    mapper.yaml              the mappings, kept beside the spec
    expected_output.yaml     run_transform's output on Row.csv

Every mapped slot is checked against the submission schema first, with the same validation
the mapper agent's output goes through; a mapping it would demote fails the build.

Usage:
    uv run python -m nmdc_metadata_suggestor_ai_tool.evaluation.transform_examples
"""

import csv
from pathlib import Path
from typing import Any

import yaml

from nmdc_metadata_suggestor_ai_tool.metadata_mapper.transform_spec import (
    compile_transform,
    dump_yaml,
    run_transform,
    write_transform_files,
)
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.validation import validate_mapper_output
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    ColumnMapping,
    MetadataMapperOutput,
    SourceFile,
)
from nmdc_metadata_suggestor_ai_tool.models.reusable_transform import ReusableTransform
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "examples" / "linkml-map"
INPUT_FILE = "Row.csv"
APPROVED_FILE = "approved_mappings.yaml"
EXPECTED_FILE = "expected_output.yaml"


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def load_approved(example_dir: Path) -> tuple[dict[str, Any], MetadataMapperOutput]:
    approved = yaml.safe_load((example_dir / APPROVED_FILE).read_text())
    file_id = example_dir.name
    output = MetadataMapperOutput(
        source_files=[SourceFile(file_id=file_id, display_name=INPUT_FILE)]
    )
    for raw in approved["mappings"]:
        mapping = ColumnMapping.model_validate({"source_file_id": file_id, **raw})
        bucket = {
            "high": output.high_confidence,
            "review": output.needs_review,
            "cant_place": output.cant_place,
        }[mapping.confidence]
        bucket.append(mapping)
    return approved, output


def build_example(
    example_dir: Path, schema_builder: SchemaContextBuilder | None = None
) -> tuple[ReusableTransform, list[dict[str, Any]]]:
    """Compile one example and run it. Writes nothing."""
    builder = schema_builder or SchemaContextBuilder()
    approved, output = load_approved(example_dir)
    placed = {m.source_column for m in output.high_confidence + output.needs_review}
    validated = validate_mapper_output(output.model_copy(deep=True), builder)
    demoted = [m for m in validated.cant_place if m.source_column in placed]
    if demoted:
        reasons = {m.source_column: m.reason for m in demoted}
        raise ValueError(f"{example_dir.name}: mappings failed validation: {reasons}")

    headers, rows = read_csv(example_dir / INPUT_FILE)
    transform = compile_transform(
        output,
        headers,
        name=example_dir.name,
        mixs_extensions=approved.get("mixs_extensions"),
        description=approved.get("description", ""),
        schema_builder=builder,
    )
    # A fixed date keeps the generated files stable from one build to the next.
    transform = transform.model_copy(update={"created": str(approved.get("created"))})
    return transform, run_transform(transform, rows)


def write_example(example_dir: Path, schema_builder: SchemaContextBuilder | None = None) -> None:
    transform, rows = build_example(example_dir, schema_builder)
    write_transform_files(transform, example_dir)
    (example_dir / EXPECTED_FILE).write_text(dump_yaml(rows))


def example_dirs() -> list[Path]:
    return sorted(p.parent for p in EXAMPLES_DIR.glob(f"*/{APPROVED_FILE}"))


def main() -> None:
    builder = SchemaContextBuilder()
    for example_dir in example_dirs():
        write_example(example_dir, builder)
        print(f"built {example_dir.relative_to(EXAMPLES_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
