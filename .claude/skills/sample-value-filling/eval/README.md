# sample-value-filling — initial evaluation

A first, hand-run evaluation of the `sample-value-filling` skill against real NMDC data. It checks
that the skill's instructions produce a schema-valid, pattern-conformant, row-addressed result, and
that the skill fills what the evidence supports while declining what it does not.

A prototype's eval stays in this repo until the skill is adopted (full eval harnesses live in
`nmdc-ai-eval`); this lives beside the skill for now.

## Source

Study **`nmdc:sty-11-5tgfr349`** — GROW / WHONDRS, "Freshwater microbial communities from rivers from
various locations all over the world." Six river-sediment biosamples were pulled from the NMDC API
(`biosample_set`), spanning the USA, UK, and Canada.

## Method

`submission.json` is a simulated submission portal object built from those six rows:

- **Kept** (submitter-style evidence): each row's real `samp_name` and free-text `description`
  (e.g. "River sediment microbial communities from Como Creek, Colorado, USA"); the study's real
  title, description, and award/dataset DOIs.
- **Blanked** (the fields under test): `geo_loc_name`, `collection_date`, `lat_lon`, `elev`,
  `habitat`.
- **Added**: one methods sentence to the study description, factual for this campaign — the WHONDRS
  Summer 2019 Synoptic Sampling (S19S), August–September 2019, surface sediments from depositional
  zones. It gives a study-wide signal for `collection_date` to test confident inference.

`ground_truth.json` holds the real biosample values for comparison. `llm_output.json` is the skill's
output, produced by executing the skill's instructions by hand (not a headless agentic run).

`score.py` reproduces the checks offline:

```bash
uv run python .claude/skills/sample-value-filling/eval/score.py
```

## Results (2026-10-21)

- **Schema:** validates as `LLMOutput`; 12 entries; no `provenance` (correct — no gate on this path).
- **Patterns:** 0 failures. Every `geo_loc_name` and `collection_date` matches its SedimentInterface
  regex.
- **`geo_loc_name`:** 6/6 correct country and state/province, extracted per row. The specific
  locality reflects the water body named in each description (e.g. "Como Creek") rather than ground
  truth's nearest town ("Nederland") — both correct, drawn faithfully from the given text.
- **`collection_date`:** 6/6 correct year (`2019`), inferred study-wide. Coarser than the exact dates
  because the study text gave only the campaign span — the honest precision, not a fabricated day.
- **Restraint:** `lat_lon`, `elev`, `depth`, `collection_time`, `samp_mat_process` were all omitted.
  No coordinates, elevations, depths, times, or processing in the evidence, so none emitted — even
  though several are *required* slots.

## Caveat

This exercises the skill's **instructions**, not the full headless agentic pipeline
(`ConversationManager.agentic` with a model and `StructuredOutput`). That path is not yet evaluated.
