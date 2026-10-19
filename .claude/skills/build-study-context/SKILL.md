---
name: build-study-context
description: Use this skill to assemble an evidence bundle (context texts) from a parsed submission or ETL study context, including DOI abstracts and PDF content.
---

# Build Study Context Skill

Use this skill to assemble an evidence bundle from a parsed submission or ETL study context. Called by both `metadata-suggestion-pipeline` and `env-triad` before making suggestions. Requires the **doi-ingestion** and **pdf-ingestion** skills.

---

## Inputs

Provide one of:

- **`parsed_submission`** — output of the **submission-parser** skill (has `description`, `notes`, `study_name`, `dois`, `protocol_descs`, `protocol_names`)
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

Then apply the **doi-copyright-check** skill to the returned metadata:

- If `verdict == "allowed"`: add `result.context` to the evidence texts (label it with the DOI value)
- If `verdict == "not_allowed"` or `"uncertain"`: **exclude** the content; add the DOI to a `skipped_dois` list and log a warning
- Accumulate all `result.publication_urls` into a list regardless of verdict (URLs themselves are not restricted content)

### 3 — Fetch PDF content

Utilize `download_pdf_to_tempfile` (from **pdf-ingestion** skill).

### 4 — (Optional) Fetch supplementary materials

For publication DOIs, optionally enrich the evidence with high-value supplements
(sample-metadata tables, supplementary methods) using the **supplement-retrieval**
skill. This is performance-first — make a single attempt per DOI and skip on
failure:

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements import (
    retrieve_supplements,
)

# Pass the DOI's abstract text (from step 2) so linked-dataset DOIs mentioned in
# the prose can also be resolved. The skill routes by DOI type and merges hosted
# supplements with any linked Dryad/Zenodo/Figshare datasets.
supp = retrieve_supplements(doi=entry["value"], text=result.context)
for f in supp.files:
    if f.text:
        context_texts.append(
            f"Supplement {f.filename} [{f.source}] (for {entry['value']}):\n{f.text}"
        )
    # non-text files are at f.saved_path for downstream readers
```

Only tabular and document supplements are kept by default. Do not retry when
supplements are unavailable.

Non-text supplements (xlsx/pdf/docx) are written to temp files, so delete them
once you have read what you need — otherwise every run leaves up to
`max_total_bytes` of them behind:

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import (
    remove_temp_files,
)

remove_temp_files([f.saved_path for f in supp.files if f.saved_path])
```

Pass `save_dir=...` to `retrieve_supplements` instead if you want to keep the
files; those are yours to manage and are never removed for you.

---

## Output

An evidence bundle — pass to whatever pipeline skill calls this:

| Part | Description |
|------|-------------|
| `context_texts` | List of strings: base text + one entry per DOI abstract + fetched PDF content. Use as evidence when reasoning. |

No cleanup required in the agentic path — `web_fetch` reads content inline without creating temp files.
The programmatic supplement path in step 4 is the exception: it writes temp files, which that step's
`remove_temp_files` call cleans up.
