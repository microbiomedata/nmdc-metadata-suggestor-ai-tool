---
name: build-study-context
description: Use this skill to assemble an evidence bundle (context texts) from a parsed submission or ETL study context, including DOI abstracts and PDF content.
---

# Build Study Context Skill

Use this skill to assemble an evidence bundle from a parsed submission or ETL study context. Called by both `metadata_suggestion_pipeline` and `env_triad_recommendation` before making suggestions. Requires the **doi_ingestion** and **pdf_ingestion** skills.

---

## Inputs

Provide one of:

- **`parsed_submission`** — output of the **submission_parser** skill (has `description`, `notes`, `study_name`, `dois`, `protocol_descs`, `protocol_names`)
- **`study_context`** — list of strings or dicts (ETL path — use when no `submission_object` is available)

---

## Steps

### 1 — Assemble base text

If `parsed_submission` is provided, build the base context string:

```
Submission description: <description>
Notes: <notes>
Study name: <study_name>
Protocol descriptions: <protocol_descs joined by '; '>
Protocol names: <protocol_names joined by '; '>
```

If only `study_context` is provided, use those strings/dicts directly as the base text. Skip to output.

### 2 — Fetch DOI abstracts

For each entry in `parsed_submission.dois`, use the **doi-ingestion** skill:

```python
result = get_doi_description_or_abstract(
    doi=entry["value"],
    sources=[entry["provider"]] if entry["provider"] else None,
    skip_classification=True,
)
```

- Add `result.context` to the evidence texts (label it with the DOI value)
- Accumulate all `result.publication_urls` into a list

### 3 — Fetch PDF content

If any `publication_urls` were collected, use the built-in `web_fetch` tool to read each URL directly — no download or temp file needed. Add the content to your evidence texts.

Only fall back to `download_pdf_to_tempfile` (from **pdf-ingestion** skill) if `web_fetch` is unavailable (non-agentic path).

---

## Output

An evidence bundle — pass to whatever pipeline skill calls this:

| Part | Description |
|------|-------------|
| `context_texts` | List of strings: base text + one entry per DOI abstract + fetched PDF content. Use as evidence when reasoning. |

No cleanup required in the agentic path — `web_fetch` reads content inline without creating temp files.
