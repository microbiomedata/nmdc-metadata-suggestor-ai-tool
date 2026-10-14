# linkml-map transform examples (issue 177 prototype)

What the Metadata Mapper's saved transforms look like, for three input shapes. Each folder is a
complete linkml-map setup: a source schema, a transform spec, the input, and the output it
produces. Design notes: [docs/linkml-map-transforms.md](../../docs/linkml-map-transforms.md).

| Example | Input | What it shows |
|---|---|---|
| [`ncbi-biosample-water`](ncbi-biosample-water) | Synthetic, NCBI BioSample MIxS water package headers (`*sample_name`, `lat_lon`, `env_medium`) | Mostly straight copies; env triad slots need an `enum_map` even when values already fit; a custom expression that turns `41.68 N 83.25 W` into `41.68 -83.25` |
| [`soil-field-sheet`](soil-field-sheet) | Synthetic lab spreadsheet (`Depth (ft)`, `Soil Temp (F)`, `Drainage`) | Every conversion type: date format, unit scale, enum map, Fahrenheit → Celsius, two columns combined into `lat_lon`; blank cells |
| [`phage-wastewater`](phage-wastewater) | Real: first 6 rows of `tests/fixtures/PRJEB13831_Phage_metadata.csv`, 232 columns | A wide template where only a few columns are mapped; a `review`-confidence mapping; whitespace trimming |

The approved mappings in each folder were written by hand for illustration. They are not mapper
agent output, and the synthetic rows describe no real samples.

## Files in each folder

| File | Written by | What it is |
|---|---|---|
| `Row.csv` | hand | The input. Named for the source class: the linkml-map CLI takes the class from the file name. |
| `approved_mappings.yaml` | hand | What a reviewer approved: `ColumnMapping`s with confidence, reason and conversion. |
| `source_schema.yaml` | generated | LinkML schema induced from `Row.csv`'s headers: one class, `Row`, one string slot per column, spelled exactly as the header is. |
| `transform.yaml` | generated | The linkml-map `TransformationSpecification`. One class derivation per submission schema interface (`SoilInterface`, ...); `target_schema` points at the nmdc-submission-schema package. |
| `mapper.yaml` | generated | The mappings, kept beside the spec so the UI and the agent can see why each column went where it did. |
| `expected_output.yaml` | generated | What the transform produces from `Row.csv`. |

Only the generated files are what `TransformLibrary.save` writes for a real transform;
`approved_mappings.yaml` and `expected_output.yaml` exist only in the examples.

## Reading a transform.yaml

| Mapper conversion | Becomes |
|---|---|
| none | `populated_from: <header>` |
| `enum_map` | `populated_from` + `value_mappings` |
| `date_format`, `unit`, `split`, `custom` | a hidden copy of the column, then `expr: iso_date(slot('src_…'), …)` (or `scale`, `split_join`, `sandboxed`) |
| `combine_columns` | hidden copies of each column, then `expr: sandboxed_combined({...}, '<expression>')` |

The hidden `src_…` slots (`hide: true`) exist because linkml-map expressions can only name
identifiers, and headers like `Depth (ft)` are not. Each copies one column under a safe name;
the expression reads it back with `slot('src_depth_ft')`. They are left out of the output.

`missing_values: ['']` makes a blank cell count as missing, so it is dropped rather than
written as an empty string.

## Running one

From Python:

```python
from nmdc_metadata_suggestor_ai_tool.metadata_mapper import run_transform
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.transform_spec import read_transform_files

transform = read_transform_files(Path("examples/linkml-map/soil-field-sheet"))
rows = run_transform(transform, csv_rows)
```

With the linkml-map CLI, from the repo root:

```bash
uv run linkml-map map-data \
  -T examples/linkml-map/soil-field-sheet/transform.yaml \
  -s examples/linkml-map/soil-field-sheet/source_schema.yaml \
  --functions src/nmdc_metadata_suggestor_ai_tool/metadata_mapper/transform_functions.py \
  examples/linkml-map/soil-field-sheet/Row.csv
```

The two agree on these examples; `tests/test_transform_examples.py` checks that for every one.
They differ on bad cells, which these examples do not have. The Python path lists each failure
under `_transform_errors`. The CLI reports nothing: a value that fails to convert keeps its raw
form, and a value missing from an enum map is dropped.

## Adding or changing an example

Edit or add `Row.csv` and `approved_mappings.yaml`, then rebuild:

```bash
make transform-examples
```

The build checks every mapping against the submission schema with the same validation the
mapper agent's output goes through, and fails on any it would demote. The tests fail if the
generated files are out of date.
