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

## Pieces

All in `metadata_mapper/transform_spec.py` unless noted.

| Function | What it does |
|---|---|
| `compile_transform` | Approved `MetadataMapperOutput` + the file's headers → `ReusableTransform` (`models/reusable_transform.py`): the approved mappings plus a linkml-map `TransformationSpecification` |
| `run_transform` | Runs a `ReusableTransform` over CSV rows through linkml-map's `ObjectTransformer` |
| `match_transform`, `TransformLibrary` | Score saved transforms against a new file's headers (case, spacing, `_`/`-` ignored); a directory of `<name>.yaml` files |
| `rebase_transform` | Re-keys a saved transform to the new file's header spellings |
| `mapper_output_from_transform` | Rebuilds mapper output for the columns a transform covers, and lists the ones it doesn't |
| `build_prior_transform_context` | Prompt text; used by `run_metadata_mapper_agentic(..., prior_transform=match)` |

### How conversions translate

linkml-map evaluates `expr` strings in a restricted evaluator with a short list of functions and
no `strptime`. The mapper's conversion types map onto it like this:

| Mapper `conversion.type` | linkml-map slot derivation |
|---|---|
| `none` | `populated_from: <column>` |
| `enum_map` | `populated_from` + `value_mappings` (native linkml-map) |
| `date_format` | `expr: iso_date(<column>, '%m/%d/%Y')` |
| `unit` | `expr: scale(<column>, '0.3048')` |
| `split` | `expr: split_join(<column>, ',')` |
| `custom` | `expr: sandboxed(<column>, '<python expression>')` |
| `combine_columns` | `expr: sandboxed_combined({"Lat": lat, "Lon": lon}, '<python expression>')` |

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

CSV headers are not always valid identifiers (`Depth (ft)`, `class`), so each transform carries
a `column_slots` table from header to a safe slot name in a source schema induced from the
headers.

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
- **linkml-map CLI.** The helper module is tagged for `linkml-map map-data --functions`, but no
  command yet writes out the schema + spec pair that CLI would need.
