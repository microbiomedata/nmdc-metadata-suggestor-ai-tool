# Does supplement retrieval change env triad suggestions? (issue 143)

Test case: study `nmdc:sty-11-e4yb9z58`, "Seasonal activities of the phyllosphere
microbiome of perennial crops", 192 biosamples, publication DOI
`10.1038/s41467-023-36515-y`.

## Where the pieces live

The evaluation harness is [`nmdc-ai-eval`](https://github.com/microbiomedata/nmdc-ai-eval)
(see issue #114). This repo holds only what is specific to the case, as importable code, so the
case is defined once and the harness imports it:

| Here (`nmdc_metadata_suggestor_ai_tool`) | There (`nmdc-ai-eval`) |
|---|---|
| `evaluation/phyllosphere.py`: identifiers, the API snapshot of the study and biosamples (`evaluation/data/`, provenance in [test-data-provenance.md](test-data-provenance.md)), the submission object the pipeline needs, the two references | `datasets/supplement-triad/run_supplement_eval.py`: runs both arms through `get_env_triad_recommendation`, scores, writes results |
| `evaluation/env_triad_scoring.py`: parsing both triad dialects (`ENVO_00000446`, capitalised labels, pipe-joined cells), joining references to samples, reading an output back by sample id, coverage, arm comparison | `triad_output_scorer.py`: ENVO relationship, hop distance, label check and composite score per suggestion, reusing `envo_scorer` |
| `publication_ingestion/supplements/context.py`: `format_supplement_context`, production code that turns a retrieval result into LLM messages | `datasets/supplement-triad/pipeline-results/`: the runs |

Run it from the eval repo:

```bash
just eval-supplement-triad                            # ~20 min, chunk size 25
just eval-supplement-triad --limit 8 --chunk-size 8   # smoke run
```

## Design

Two arms, same model, same samples, same prompt. Each biosample is sent as its NMDC record
with the three triad slots removed.

- **publication**: study name and description, the Crossref abstract for the publication DOI
  (fetched by the pipeline's submission path; the award DOI resolves to a funding record with
  no text), plus the stripped biosample records.
- **publication+supplements**: the same, plus `format_supplement_context(retrieve_supplements(doi))`:
  an inventory of the 10 files kept and the full text of the two csv files (215,276 characters).

Two references, scored per suggestion with the `envo_scorer` formula: 0.1 for parsing, 0.1 for
a CURIE whose ENVO label matches, 0.5 for ontology proximity (exact 1.0, descendant 1 minus 0.1
per hop, ancestor 1 minus 0.15 per hop, unrelated 0), 0.3 for membership in the extension's
curated value set as judged by the validation gate.

- **supplement**: the authors' triad per sample in `41467_2023_36515_MOESM4_ESM.csv`. Its
  `env_medium` holds two terms; a prediction is scored against the closer one.
- **nmdc**: the triad currently stored on each biosample.

## Result (chunk size 25, 2026-09-11, gemini-2.5-flash via Vertex)

Coverage: the publication arm answered 192/192, the supplement arm 191/192 (one id returned
twice, one not at all, and 51 duplicated suggestions), no chunk errors. Wall time 364 s and
825 s.

Mean ontology score, with the exact-match rate in parentheses:

| Slot | Reference | publication | publication+supplements |
|---|---|---|---|
| env_broad_scale | supplement | 0.900 (0%) | 0.974 (74%) |
| env_local_scale | supplement | 0.591 (26%) | 1.000 (100%) |
| env_medium | supplement | 0.500 (0%) | 0.570 (35%) |
| env_broad_scale | nmdc | 0.500 (0%) | 0.500 (0%) |
| env_local_scale | nmdc | 0.461 (0%) | 0.500 (0%) |
| env_medium | nmdc | 0.500 (0%) | 0.395 (0%) |

What each arm said:

| Slot | publication | publication+supplements |
|---|---|---|
| env_broad_scale | `cropland biome [ENVO:01000245]` ×192 | `terrestrial biome [ENVO:00000446]` ×142, `cropland biome` ×49 |
| env_local_scale | `crop canopy [ENVO:01001241]` ×117, `area of cropland [ENVO:01000892]` ×50, `agricultural field [ENVO:00000114]` ×25 | `area of cropland` ×191 |
| env_medium | `leaf [PO:0025034]` ×192 | `leaf [PO:0025034]` ×124, `plant matter [ENVO:01001121]` ×67 |

Change from adding supplements, graded by ENVO proximity to the supplement, over the 191
samples answered in both arms:

| Slot | changed | toward supplement | away from supplement |
|---|---|---|---|
| env_broad_scale | 142 | 142 | 0 |
| env_local_scale | 141 | 141 | 0 |
| env_medium | 67 | 67 | 0 |

## Reading it

**Supplements change the answer, and only toward the supplement.** Not one sample moved away
across three slots and 350 changes.

**The decision is made per chunk, not per sample.** In every run so far, every sample in a
chunk got the same triad. The 49 `cropland biome` answers in the supplement arm are two whole
chunks of eight that ignored the table; the 67 `plant matter` answers are three chunks that
followed it. Percentages here are really chunks out of eight, and a rerun moves them by a chunk.
An earlier chunk-25 run had zero `plant matter`; a chunk-8 smoke run had all eight.

**The ontology score changes the picture for `env_broad_scale`.** On exact match the
publication arm scores 0. On the ontology score it is 0.900, because `cropland biome` sits two
hops under the table's `terrestrial biome` and is in the curated set. Following the supplement
gained 0.074 and traded a more specific term for the authors' broader one. Whether that is an
improvement depends on which reference you trust; the ticket names the supplement as the curated
ground truth, and by that standard it is.

**`env_medium` is where the model argues with the table.** The table says
`agricultural soil | plant matter` for a leaf-surface sample. In 124 of 191 cases the model kept
`leaf [PO:0025034]` and said why: *"'agricultural soil' is incorrect for a leaf sample, and
'plant matter' is too broad"*. `leaf` is in the curated plant-associated set; neither supplement
term is. Scoring is ENVO-only, so a PO term earns parse and enum credit and nothing for
proximity, which is why the nmdc column drops when the model switches to `plant matter`.

**The NMDC reference cannot exact-match anything.** All 192 biosamples carry the same three
values and each pairs a label with a CURIE for a different term (`agricultural biome
[ENVO:01001442]` where `ENVO:01001442` is `agriculture`; `phyllosphere biome [ENVO:01001442]`;
`plant-associated biome [ENVO:01001001]` where the CURIE is `plant-associated environment`).
Those records were last modified 2026-09-09 and need a curation pass regardless of this tool.

**The gate handled the supplement's dialect.** When the model copied
`Terrestrial Biome [ENVO_00000446]` verbatim (an earlier chunk-50 run), the validation gate
repaired it to `terrestrial biome [ENVO:00000446]` and recorded `outcome: repaired`.

**Cost.** The supplement arm took 2.3× the wall time because every chunk carries the full
215 KB of csv. Rows for only the chunk's samples would cut that; the `sample_name` join is exact,
so that is a cheap filter to add.

## The chunk-50 run, and why the runner defaults to 25

The pipeline default `chunk_size=50` was tried first and could not be scored: in one arm the
model returned 148 triads with no `id` field for a 42-sample chunk, in the other one chunk's
JSON was truncated mid-string and failed validation and another chunk folded fifty samples into
one unlabeled triad. On the 50 samples both arms did answer, all 50 moved toward the supplement
on every slot. Both failures are id-fidelity and output-length problems in the existing pipeline
with full NMDC records, not something the supplements introduced. The runner reports coverage
first and defaults to 25. The pipeline default is unchanged; that is a separate decision.

## What this does not cover

- One model (Gemini 2.5 Flash, temperature 0.4), one run at each chunk size. Chunk-level
  decisions make the numbers coarse.
- Only the two csv supplements reached the model. The eight xlsx/pdf files were retrieved but
  the pipeline has no reader for them outside a submission's PDF path.
- No arm with the publication PDF: Crossref returned no full-text link for this DOI.
- Scoring is ENVO-only; PO and UBERON values get no proximity credit.
