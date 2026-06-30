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

| Condition | Route to |
|---|---|
| Input explicitly names `env_triad`, `env_broad_scale`, `env_local_scale`, or `env_medium` | `env-triad-recommendation` only |
| Input is samples only (no submission object) | `env-triad-recommendation` only |
| Input is a submission object with no sample list | `metadata-suggestion-pipeline` only |
| Input is a submission object that includes samples | Both — run `metadata-suggestion-pipeline` first, then `env-triad-recommendation` |

---

## Step 3 — Execute and return

Run the routed skill(s) and return a single `LLMOutput` JSON object.

If both pipelines run, merge their `metadata_fields` lists into one response.
