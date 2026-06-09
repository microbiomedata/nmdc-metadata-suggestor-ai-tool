# Submission Parser Skill

Use this skill to extract structured fields from a raw NMDC submission portal object. Called at the start of both `metadata_suggestion_pipeline` and `env_triad_recommendation` when a `submission_object` is present.

---

## Input

A raw submission portal dict with a `metadata_submission` key.

---

## What to extract

Parse the following fields from `metadata_submission`:

| Output field | Source path |
|---|---|
| `study_name` | `studyForm.studyName` |
| `description` | `studyForm.description` |
| `notes` | `studyForm.notes` |
| `gold_study_id` | `studyForm.GOLDStudyId` |
| `jgi_study_id` | `multiOmicsForm.JGIStudyId` |
| `dois` | Merge and deduplicate `studyForm.dataDois`, `studyForm.publicationDois`, `multiOmicsForm.awardDois` — each entry is `{"value": "10.x/y", "provider": "osti" \| null}` |
| `protocol_descs` | All `externalProtocol.description` values across `mpProtocols`, `mbProtocols`, `mbGcProtocols`, `lipProtocols`, `nomProtocols`, `nomLcProtocols` |
| `protocol_names` | All `externalProtocol.name` values from same sections |
| `mixs_extensions` | Interface class names derived from the `sampleData` keys or declared extensions (e.g. `["SoilInterface"]`) |

### Deduplication

Deduplicate DOIs by `(value, provider)` pair. Preserve original order.

### Protocol extraction

For each protocol section in `multiOmicsForm`:
```
for section in [mpProtocols, mbProtocols, mbGcProtocols, lipProtocols, nomProtocols, nomLcProtocols]:
    protocols = multiOmicsForm.get(section) or {}
    for protocol in protocols values:
        external = protocol.get("externalProtocol", {})
        collect external.description and external.name if present
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

Pass this structured dict to the **build_study_context** skill.
