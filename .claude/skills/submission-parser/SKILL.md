---
name: submission-parser
description: Use this skill to extract structured fields (DOIs, study name, description, protocol info, MIxS extensions) from a raw NMDC submission portal object. Called at the start of metadata-suggestion-pipeline and env-triad when a submission_object is present.
---

# Submission Parser Skill

Use this skill to extract structured fields from a raw NMDC submission portal object. Called at the start of both `metadata-suggestion-pipeline` and `env-triad` when a `submission_object` is present.

---

## Input

A raw submission portal payload in the shape `{"submission": {...}, "sample_set": {...}}` (where `sample_set` is optional).

---

## What to extract

Parse from `submission` and `sample_set`:

| Output field | Source path |
|---|---|
| `study_name` | `submission.study_form.studyName` |
| `description` | `submission.study_form.description` |
| `notes` | `submission.study_form.notes` |
| `gold_study_id` | `submission.study_form.GOLDStudyId` |
| `ncbi_bio_project` | `submission.study_form.NCBIBioProjectId` |
| `jgi_study_id` | `sample_set.multi_omics_form.JGIStudyId` |
| `dois` | Merge and deduplicate `submission.study_form.dataDois`, `submission.study_form.publicationDois`, `sample_set.multi_omics_form.awardDois`, and any `doi` strings from protocol entries (as `{"value": doi, "provider": null}`) |
| `protocol_descs` | All `description` values across `mpProtocols`, `mbProtocols`, `mbGcProtocols`, `lipProtocols`, `nomProtocols`, `nomLcProtocols` in `sample_set.multi_omics_form` |
| `protocol_names` | All `name` values from the same protocol sections |
| `mixs_extensions` | Interface class names mapped from `sample_set.sample_environment_form.packageName` (e.g. `"soil"` → `"SoilInterface"`) |

### Deduplication

Deduplicate DOIs by `(value, provider)` pair. Preserve original order.

### Protocol extraction

For each protocol section in `sample_set.multi_omics_form`:
```
for section in [mpProtocols, mbProtocols, mbGcProtocols, lipProtocols, nomProtocols, nomLcProtocols]:
    protocols = multi_omics_form.get(section) or {}
    for protocol in protocols.values():
        collect protocol.get("doi"), protocol.get("description"), and protocol.get("name") if present and non-empty
```

---

## Output

A structured dict:

```json
{
  "study_name": "Soil metabolome response...",
  "description": "In this study...",
  "notes": "",
  "gold_study_id": "",
  "jgi_study_id": "510963",
  "dois": [
    {"value": "10.1073/pnas.2004192118", "provider": "osti"},
    {"value": "10.46936/10.25585/60013043", "provider": "jgi"}
  ],
  "protocol_descs": ["Proteomics samples were separated..."],
  "protocol_names": ["proteomics lc", "metabolome lc"],
  "mixs_extensions": ["SoilInterface"]
}
```

Pass this structured dict to the **build-study-context** skill.
