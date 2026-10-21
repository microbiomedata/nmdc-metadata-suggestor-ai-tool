---
name: nmdc-metadata-suggestor
description: Use this skill to orchestrate NMDC metadata suggestions — detects input shape, routes to env-triad and/or metadata-suggestion-pipeline, and returns a merged LLMOutput JSON. Entry point for all metadata suggestion requests.
---

# NMDC Metadata Suggestor Orchestration Skill

Receives data, determines what to run, returns `LLMOutput` JSON. No user interaction.

---

## Step 1 — Detect input shape

| Condition | Shape |
|---|---|
| Input has top-level key `metadata_submission` | Submission portal object |
| Input is a list of dicts with biosample-like keys (`id`, `lat_lon`, env fields) | ETL samples |
| Input is a list of dicts with MIxS column-name keys | Submission portal-shaped samples |

---

## Step 2 — Detect requested output

Three skills fill values, each owning a distinct slice so their suggestions never collide:

- `metadata-suggestion-pipeline` — study/submission-level fields (`id: null`)
- `env-triad` — `env_broad_scale`, `env_local_scale`, `env_medium`, per row
- `sample-value-filling` — every other fillable MIxS slot, per row

Evaluate top to bottom and take the first row that matches. The field-name rows are ordered so a
request that names **both** a triad and a non-triad field (e.g. "suggest `env_medium` and `depth`")
falls through to the second row and runs both fillers — it is not captured by the triad-only row.

| Condition | Route to |
|---|---|
| Input explicitly names at least one non-triad field, or asks for "row" / "per-sample" values | `sample-value-filling` — and also `env-triad` if a triad field is named too |
| Input explicitly names only env triad field(s) (`env_triad`, `env_broad_scale`, `env_local_scale`, `env_medium`) and no other field | `env-triad` only |
| Input is samples only (no submission object) | `env-triad` and `sample-value-filling` |
| Input is a submission object with no sample list | `metadata-suggestion-pipeline` only |
| Input is a submission object that includes samples | All three — run `metadata-suggestion-pipeline` first (submission-level), then `env-triad` and `sample-value-filling` (both per row) |

---

## Step 3 — Execute and return

Run the routed skill(s) and return a single `LLMOutput` JSON object.

If more than one skill runs, merge their `metadata_fields` lists into one response. The slices do not
overlap, so no de-duplication is needed: submission-level entries carry `id: null`, the three triad
fields come only from `env-triad`, and all other per-row fields come only from `sample-value-filling`.

**IMPORTANT: Do NOT call StructuredOutput until all routed skills have finished executing. Call StructuredOutput exactly once at the very end with the complete merged `LLMOutput` JSON.**
