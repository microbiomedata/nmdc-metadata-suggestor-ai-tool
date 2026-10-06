# Reusable linkml-map transforms for the Metadata Mapper (prototype)

Draft for [issue 177](https://github.com/microbiomedata/nmdc-metadata-suggestor-ai-tool/issues/177).
The mapper agent works out every column of every upload from scratch. When a later submission
has the same shape as one already mapped and approved, that work could be reused. This
prototype compiles an approved mapping into a [linkml-map](https://github.com/linkml/linkml-map)
transform spec, saves it, recognizes later files with the same columns, and either hands the
saved mapping to the agent or runs it without the agent at all.

## Flow

```
upload ─► headers ─► TransformLibrary.find_match ─┬─ no match ─► agent (as today)
                                                  │
                                                  └─ match ─┬─► agent + saved mappings in prompt
                                                            └─► mapper_output_from_transform
                                                                + run_transform (no LLM)
approved MetadataMapperOutput ─► compile_transform ─► TransformLibrary.save
```

## Files

A saved transform is a folder of ordinary linkml-map files, so it runs with the linkml-map CLI as
well as from Python:

| File | What it is |
|---|---|
| `source_schema.yaml` | LinkML schema induced from the CSV headers: one class, `Row`, one string slot per column, named exactly as the header is spelled, so the CLI can read the raw CSV |
| `transform.yaml` | linkml-map `TransformationSpecification`. One class derivation per submission schema interface the mappings use (`WaterInterface`, `SoilInterface`, ...), each `populated_from: Row`. `target_schema` points at the nmdc-submission-schema package (name, version, file) |
| `mapper.yaml` | The approved mappings with confidence and reasons, plus the source columns and extensions |

```bash
linkml-map map-data -T transform.yaml -s source_schema.yaml \
  --functions src/nmdc_metadata_suggestor_ai_tool/metadata_mapper/transform_functions.py Row.csv
```

The CLI takes the source class from the input file's name, so the CSV has to be called `Row.csv`.
Worked examples for three input shapes, with expected output, are in
[examples/linkml-map/](../examples/linkml-map/README.md).

The target schema is referenced, not loaded at run time. Slot names are checked against it
before a transform is built, by the mapper's own validation step (`validate_mapper_output`).

## Pieces

All in `metadata_mapper/transform_spec.py` unless noted.

| Function | What it does |
|---|---|
| `compile_transform` | Approved `MetadataMapperOutput` + the file's headers → `ReusableTransform` (`models/reusable_transform.py`): the approved mappings, the induced source schema, and the spec |
| `run_transform` | Runs a `ReusableTransform` over CSV rows through linkml-map's `ObjectTransformer` |
| `match_transform`, `TransformLibrary` | Score saved transforms against a new file's headers (case, spacing, `_`/`-` ignored); a directory of transform folders |
| `write_transform_files`, `read_transform_files` | Save and load one transform folder |
| `rebase_transform` | Rebuilds a saved transform for a new file's header spellings, including column names inside combine expressions |
| `mapper_output_from_transform` | Rebuilds mapper output for the columns a transform covers, and lists the ones it doesn't |
| `build_prior_transform_context` | Prompt text; used by `run_metadata_mapper_agentic(..., prior_transform=match)` |

### How conversions translate

linkml-map evaluates `expr` strings in a restricted evaluator with a short list of functions and
no `strptime`. The mapper's conversion types map onto it like this:

| Mapper `conversion.type` | linkml-map slot derivation |
|---|---|
| `none` | `populated_from: <header>` |
| `enum_map` | `populated_from` + `value_mappings` (native linkml-map) |
| `date_format` | `expr: iso_date(slot('src_<column>'), '%m/%d/%Y')` |
| `unit` | `expr: scale(slot('src_<column>'), '0.3048')` |
| `split` | `expr: split_join(slot('src_<column>'), ',')` |
| `custom` | `expr: sandboxed(slot('src_<column>'), '<python expression>')` |
| `combine_columns` | `expr: sandboxed_combined({"Lat": slot('src_lat'), "Lon": slot('src_lon')}, '<python expression>')` |

Expressions can only name identifiers, and headers like `Depth (ft)` are not. So each column an
expression needs is first copied into a hidden slot (`src_depth_ft: {populated_from: "Depth
(ft)", hide: true}`), declared before the slot that reads it with `slot('src_depth_ft')`.
Hidden slots are left out of the output. Every `populated_from` carries `missing_values: ['']`,
so blank cells are dropped rather than written as empty strings.

The helpers live in `metadata_mapper/transform_functions.py` and are registered as linkml-map
extension functions. Each one calls the existing `ValueTransformer`, so:

- a spec run and `apply_mappings` give the same values (tested, cell for cell, on every type);
- agent-written `custom` code still runs in the RestrictedPython sandbox with its timeout, not in
  linkml-map's evaluator. The "helper functions an agent has to write" are these expressions,
  stored as strings in the spec. Promoting a recurring one to a named, reviewed helper is a
  natural next step.

A bad cell does not stop the row: the helper records the error under `_transform_errors` and keeps
the raw value, as `apply_mappings` does. linkml-map's `value_mappings` returns nothing on a miss
rather than failing, so `run_transform` checks for that and reports it the same way.

Headers containing `.` are refused for now: linkml-map reads a dot in `populated_from` as
`table.column`.

## Eval

`evaluation/transform_reuse.py`, run with `make eval-transform-reuse [ARGS="--reps 3"]`. It
lives here, not in nmdc-ai-eval, while this is a prototype; `evaluation/mapper_comparison.py`
(the scorer) and the runner can move there as they are if the approach is adopted.

The phage CSV fixture (18 rows) is split into `earlier` and `later` halves with the same
columns. The transform is learned from the agent's output on `earlier` (treated as approved,
no review) or loaded with `--transform`. Then, on `later`:

| arm | agent? | context |
|---|---|---|
| `cold` | yes | columns and sample values, as today |
| `with_transform` | yes | the same plus the saved mappings |
| `transform_only` | no | the saved transform through linkml-map |

Reported per arm: wall time, turns, input/output/cache tokens and cost (from the run's
`ResultMessage`, now kept on `MetadataMapperOutput.run_health`), confidence counts, slot and
conversion agreement with cold run 1, cell-level value agreement after transforming, and
transform errors. With `--reps 2` or more it also reports how well runs of the same arm agree
with each other. The cold-vs-cold figure is the noise floor, and `with_transform` agreement
means little without it. `--perturb-headers` respells `later`'s headers (`geo_loc_name_country` →
`Geo Loc Name Country`) to exercise matching.

Output goes to `evaluation-results/transform-reuse/<timestamp>/` (gitignored).

## Not done / open

- **No human review in the eval.** The learned transform is unreviewed agent output, so the eval
  measures reuse and consistency, not correctness. A hand-checked mapping for the phage file
  would allow a real accuracy score.
- **One small file.** 18 rows, 21 non-empty columns. The issue's real targets (NCBI, JGI, EMSL
  shapes) need their own example files and saved transforms; `TransformLibrary` has none yet.
- **Messy data** (issue's open question). A saved `date_format` that no longer fits shows up as
  per-cell errors from `run_transform`, and the prompt tells the agent to re-check conversions
  whose sample values no longer fit. Nothing yet decides automatically to fall back.
- **Server wiring.** Nothing calls `TransformLibrary` from the server yet; where saved transforms
  are stored and who approves them is open.
- **Enum checks.** `apply_mappings` can check values against permissible values with a
  `schema_builder`; `run_transform` does not yet.
- **CLI error reporting.** The CLI runs the saved files, but it does not report bad cells: a
  value that fails to convert keeps its raw form, and one missing from an enum map is dropped.
  `run_transform` reports both under `_transform_errors`.
- **Readability.** A combine expression nested inside a linkml-map `expr` comes out with doubled
  quotes in YAML. Valid, but hard to read; promoting such expressions to named helpers would fix it.
