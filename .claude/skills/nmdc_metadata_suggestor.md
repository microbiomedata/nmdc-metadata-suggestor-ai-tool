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
| Input explicitly names `env_triad`, `env_broad_scale`, `env_local_scale`, or `env_medium` | `env_triad_recommendation` only |
| Input is samples only (no submission object) | `env_triad_recommendation` only |
| Input is a submission object with no sample list | `metadata_suggestion_pipeline` only |
| Input is a submission object that includes samples | Both — run `metadata_suggestion_pipeline` first, then `env_triad_recommendation` |

---

## Step 3 — Apply progressive context loading

Load skills only as the input warrants:

1. **Submission object present** → run **submission_parser** to extract structured fields
2. **DOIs found** → run **doi_ingestion** for each DOI (`skip_classification=True`, pass `provider` when known)
3. **`publication_urls` returned** → use built-in `web_fetch` to read PDF content inline (see **pdf_ingestion** skill)
4. **Interface type identifiable** → narrow schema to those interfaces; otherwise load all
5. **Schema always needed** → run **schema_skill** (`format_env_triad_context` or `format_multi_interface_context`)

---

## Step 4 — Execute and return

Run the routed skill(s) and return a single `LLMOutput` JSON object.

If both pipelines run, merge their `metadata_fields` lists into one response.
