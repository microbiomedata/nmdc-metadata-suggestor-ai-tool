"""The worked linkml-map examples in examples/linkml-map/ stay in sync and run with the CLI."""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from nmdc_metadata_suggestor_ai_tool.evaluation.transform_examples import (
    EXPECTED_FILE,
    INPUT_FILE,
    build_example,
    example_dirs,
)
from nmdc_metadata_suggestor_ai_tool.metadata_mapper import transform_functions
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.transform_spec import (
    MAPPER_FILE,
    SOURCE_SCHEMA_FILE,
    SPEC_FILE,
    dump_yaml,
    read_transform_files,
)
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder

EXAMPLES = example_dirs()
LINKML_MAP = Path(sys.executable).parent / "linkml-map"


@pytest.fixture(scope="module")
def builder() -> SchemaContextBuilder:
    return SchemaContextBuilder()


def test_examples_exist() -> None:
    assert {p.name for p in EXAMPLES} >= {
        "ncbi-biosample-water",
        "phage-wastewater",
        "soil-field-sheet",
    }


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_generated_files_are_current(example: Path, builder: SchemaContextBuilder) -> None:
    """Fails when the code or approved_mappings.yaml changed and the example was not rebuilt.

    Rebuild with: uv run python -m nmdc_metadata_suggestor_ai_tool.evaluation.transform_examples
    """
    transform, rows = build_example(example, builder)
    assert read_transform_files(example) == transform
    data = transform.model_dump(mode="json")
    assert (example / SOURCE_SCHEMA_FILE).read_text() == dump_yaml(data.pop("source_schema"))
    assert (example / SPEC_FILE).read_text() == dump_yaml(data.pop("spec"))
    assert (example / MAPPER_FILE).read_text() == dump_yaml(data)
    assert (example / EXPECTED_FILE).read_text() == dump_yaml(rows)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_linkml_map_cli_reproduces_expected_output(example: Path, tmp_path: Path) -> None:
    """The saved files are plain linkml-map: the CLI runs them as they are."""
    out = tmp_path / "out.yaml"
    subprocess.run(
        [
            str(LINKML_MAP),
            "map-data",
            "-T",
            str(example / SPEC_FILE),
            "-s",
            str(example / SOURCE_SCHEMA_FILE),
            "--functions",
            transform_functions.__file__,
            "-f",
            "yaml",
            "-o",
            str(out),
            str(example / INPUT_FILE),
        ],
        check=True,
        capture_output=True,
    )
    cli_rows = [
        {k: v for k, v in row.items() if v is not None}
        for row in yaml.safe_load_all(out.read_text())
        if row is not None
    ]
    expected = yaml.safe_load((example / EXPECTED_FILE).read_text())
    assert cli_rows == expected
