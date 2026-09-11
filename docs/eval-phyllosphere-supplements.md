# Does supplement retrieval change env triad suggestions? (issue 143)

Test case: study `nmdc:sty-11-e4yb9z58`, "Seasonal activities of the phyllosphere
microbiome of perennial crops", 192 biosamples, publication DOI
`10.1038/s41467-023-36515-y`. Fixtures and their provenance are described in
[test-data-provenance.md](test-data-provenance.md).

Regenerate with:

```bash
make eval-phyllosphere                      # full run, chunk size 25, ~18 min on Gemini 2.5 Flash
make eval-phyllosphere EVAL_ARGS="--limit 8 --chunk-size 8"   # smoke run, ~2 min
```

The runner is `scripts/eval_phyllosphere_supplements.py`; scoring lives in
`nmdc_metadata_suggestor_ai_tool.evaluation.env_triad_scoring` and is unit tested.

## Design

Two arms, same model, same samples, same prompt. Each biosample is sent as its NMDC
record with `env_broad_scale`, `env_local_scale` and `env_medium` removed.

| Arm | Context the model sees |
|---|---|
| **publication** | study name and description, the Crossref abstract for the publication DOI (fetched by the pipeline's submission path), nothing for the award DOI (it resolves to a funding record with no text), plus the stripped biosample records |
| **publication+supplements** | the same, plus `format_supplement_context(retrieve_supplements(doi))`: an inventory of the 10 files kept and the full text of the two csv files (215,276 characters) |

Two references, scored on the CURIE:

- **supplement**: the triad the authors wrote per sample in `41467_2023_36515_MOESM4_ESM.csv`,
  after normalizing `ENVO_00000446` to `ENVO:00000446`, ignoring label case, and splitting
  the pipe-joined `env_medium` (a hit on either term counts).
- **nmdc**: the triad currently stored on each biosample.

The interface is named (`plant-associated`), so the validation gate scores tiers against
that extension's curated value sets.

## Result (chunk size 25, 2026-09-11, gemini-2.5-flash via Vertex)

Coverage first, because a missing answer counts as a miss below:

| Arm | samples answered | suggestions | without id | chunk errors | wall time |
|---|---|---|---|---|---|
| publication | 192/192 | 576 | 0 | 0 | 312 s |
| publication+supplements | 191/192 | 576 | 0 | 0 | 727 s |

(The supplement arm returned one id twice and one sample not at all.)

| Slot | Reference | publication | publication+supplements |
|---|---|---|---|
| env_broad_scale | supplement | 0/192 (0%) | 142/192 (74%) |
| env_local_scale | supplement | 75/192 (39%) | 166/192 (86%) |
| env_medium | supplement | 0/192 (0%) | 0/192 (0%) |
| all three | nmdc | 0/192 | 0/192 |

What each arm actually said:

| Slot | publication | publication+supplements |
|---|---|---|
| env_broad_scale | `cropland biome [ENVO:01000245]` ×192 | `terrestrial biome [ENVO:00000446]` ×142, `cropland biome` ×49 |
| env_local_scale | `crop canopy [ENVO:01001241]` ×117, `area of cropland [ENVO:01000892]` ×75 | `area of cropland` ×166, `crop canopy` ×25 |
| env_medium | `leaf [PO:0025034]` ×192 | `leaf [PO:0025034]` ×191 |

Every value in both arms was accepted by the gate as `submission_enum`, i.e. from the
plant-associated curated value set.

Change from adding supplements, per sample answered in both arms (191):

| Slot | changed | toward supplement | away from supplement |
|---|---|---|---|
| env_broad_scale | 142 | 142 | 0 |
| env_local_scale | 91 | 91 | 0 |
| env_medium | 0 | 0 | 0 |

## Reading it

**Supplements change the answer, and only toward the supplement.** Not one sample moved
away from the authors' values. For `env_local_scale` the table settled a coin flip: without
it the model split between `crop canopy` and `area of cropland` by chunk, with it 166 of 191
took the table's `area of cropland`.

**The decision is made per chunk, not per sample.** In every run, every sample in a chunk
got the same triad. The 49 `cropland biome` answers in the supplement arm are two whole
chunks (of eight) that ignored the table's `Terrestrial Biome`; the 25 `crop canopy` answers
are one chunk. So the accuracy numbers are really "6 of 8 chunks" and "7 of 8 chunks", and a
different chunking would give different percentages. Any per-sample scoring of this pipeline
inherits that granularity.

**`env_medium` never moved, and that looks like the right call.** The table says
`agricultural soil [ENVO_00002259] | plant matter [ENVO_01001121]` for a leaf-surface
sample. The model's stated reason, in the supplement arm: *"'agricultural soil' is incorrect
for a leaf sample, and 'plant matter' is too broad. 'leaf [PO:0025034]' is a more specific"*.
`leaf` is in the curated plant-associated value set; neither supplement term is, and the
prompt says to prefer the curated set. In the chunk-50 run two chunks did switch to
`plant matter [ENVO:01001121]` (tier `envo_expansion`), so this is a preference and not a
rule.

**`env_broad_scale` moved to a broader term.** `cropland biome` is a subclass of
`terrestrial biome` and is arguably the better answer for switchgrass plots. Following the
supplement here traded specificity for agreement with the authors. Whether that is an
improvement depends on which reference you trust; the ticket names the supplement values
as the curated ground truth, and by that standard it is.

**The NMDC reference scores zero everywhere, and cannot do otherwise.** All 192 biosamples
carry the same three values and each pairs a label with a CURIE for a different term
(`agricultural biome [ENVO:01001442]` where `ENVO:01001442` is `agriculture`;
`phyllosphere biome [ENVO:01001442]`; `plant-associated biome [ENVO:01001001]` where the
CURIE is `plant-associated environment`). `env_local_scale` also reuses the broad-scale
CURIE. The gate would reject or repair all three. Those records were last modified
2026-09-09; they need a curation pass regardless of this tool.

**The gate handled the supplement's dialect.** When the model copied
`Terrestrial Biome [ENVO_00000446]` verbatim (50 suggestions in the chunk-50 run), the
validation gate repaired it to `terrestrial biome [ENVO:00000446]` and recorded
`outcome: repaired`. Nothing upstream needed to normalize underscores or case.

**Cost.** The supplement arm took 2.3× the wall time (727 s vs 312 s) because every chunk
carries the full 215 KB of csv. Rows for only the chunk's samples would cut that; the
`sample_name` join is exact, so that is a cheap filter to add.

## The chunk-50 run, and why the default is 25

The first full run used the pipeline default `chunk_size=50`. It is not usable for scoring:

| Arm | samples answered | suggestions | without id | chunk errors |
|---|---|---|---|---|
| publication | 150/192 | 894 | 444 | 0 |
| publication+supplements | 92/192 | 279 | 3 | 1 |

- publication, chunk 4 (42 samples): the model returned 148 triads with no `id` field.
- publication+supplements, chunk 1: response was truncated mid-string at line 2995 of the
  JSON and failed validation, so the chunk produced nothing.
- publication+supplements, chunk 3: fifty samples collapsed into one unlabeled triad.

On the 50 samples both arms did answer, the direction matched the chunk-25 run: all 50
moved toward the supplement on every slot, including `env_medium` → `plant matter`.

Both failures are id-fidelity and output-length problems in the existing pipeline at chunk
size 50 with full NMDC records, not something the supplements introduced (the publication
arm lost a chunk too). The evaluation runner defaults to 25, where both arms answered
191 to 192 of 192. The pipeline default is unchanged; that is a separate decision.

## What this does not cover

- One model (Gemini 2.5 Flash, temperature 0.4), one run per chunk size. Chunk-level
  decisions make the percentages coarse; a second seed would likely move them by a chunk.
- Only the two csv supplements reached the model. The eight xlsx/pdf files were retrieved
  but the pipeline has no reader for them outside a submission's PDF path.
- No arm with the publication PDF: Crossref returned no full-text link for this DOI, so
  the publication arm is abstract only.
- The `nmdc-ai-eval` harness (issue 55 there) is not wired in; this is a standalone
  script with the scoring in a reusable module so it can be.
