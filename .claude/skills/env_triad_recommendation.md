# Env Triad Recommendation Skill

Use this skill to suggest values for `env_broad_scale`, `env_local_scale`, and `env_medium` for one or more NMDC biosample records. You are the recommender — execute all steps yourself.

---

## Goal

For each input sample, you must produce three metadata field suggestions:
- `env_broad_scale` — broad ecological biome (e.g. `"temperate grassland biome [ENVO:01000196]"`)
- `env_local_scale` — local environmental feature (e.g. `"agricultural field [ENVO:00000114]"`)
- `env_medium` — environmental material (e.g. `"soil [ENVO:00001998]"`)

Values must use the format `"label [CURIE]"` from the NMDC schema enumerations.

---

## Step 1 — Assign sample IDs
For sample level suggestions, the suggestion must be tied to an ID. Copy the ID verbatiem if it exists in the input data. If samples are missing an `id` field, assign a string index (`"0"`, `"1"`, ...). Never fabricate or normalize existing IDs — copy them verbatim.

---

## Step 2 — Gather study context

**If a `submission_object` is provided:**
1. Use the **submission_parser** skill to extract structured fields
2. Use the **build_study_context** skill to fetch DOI abstracts and PDFs

**If `study_context` is provided (ETL path):**
1. Use the **build_study_context** skill with `study_context` directly — no parsing needed

**If neither is provided:** use only the sample data itself as evidence.

---

## Step 3 — Identify MIxS interface(s)

Determine which environment type(s) apply from the samples and context. Use these values:

| Value | Environment |
|---|---|
| `soil` | Soil |
| `water` | Water |
| `air` | Air |
| `sediment` | Sediment |
| `built environment` | Built environment |
| `host-associated` | Host-associated |
| `microbial mat_biofilm` | Microbial mat / biofilm |
| `plant-associated` | Plant-associated |
| `hydrocarbon resources-cores` | Hydrocarbon cores |
| `hydrocarbon resources-fluids_swabs` | Hydrocarbon fluids/swabs |
| `miscellaneous natural or artifical environment` | Miscellaneous |

If uncertain, use all interfaces.

---

## Step 4 — Load schema context

Use the **schema_skill**: call `SchemaContextBuilder().format_env_triad_context(class_names)` with the interface class names (e.g. `["SoilInterface"]`) to get the allowed values and definitions for the three env triad slots.

---

## Step 5 — Suggest env triad values

Process samples in chunks of 50 if there are many. For each sample, use all gathered evidence to suggest values. Apply these rules:
**Value rules:**
- Use the `"label [CURIE]"` format from the NMDC schema enumerations
- Always populate `value` when you can identify an appropriate ENVO term — from sample record fields, geographic info, habitat descriptions, or study context
- If the exact term is already present in the sample record (e.g. an existing `env_broad_scale` field), copy it verbatim into `value`
- If you quote a value in `reason`, that value is explicit — put it in `value` too
- Do not leave `value` empty, instead suggest the most generic term relevant if there is not sufficient information for a moer specific term. 

**Reason rules:**
- Every `reason` must cite specific input text (exact short quote up to 12 words, or paraphrase with source label such as "abstract", "study description", "sample field")
- No generic domain justifications without citing input text
- Do not reference schema package names (e.g. `SoilInterface`) in reasons
- Omit a field entirely if there is no strong, evidence-backed justification
---

## Output format

Return a JSON object matching `LLMOutput`. For each sample, emit one entry per env triad field:

```json
{
  "metadata_fields": [
    {
      "id": "nmdc:bsm-11-abc123",
      "field_name": "env_broad_scale",
      "value": "temperate grassland biome [ENVO:01000196]",
      "reason": "'peatland' and 'warming treatment' in study description indicate a boreal peatland biome."
    },
    {
      "id": "nmdc:bsm-11-abc123",
      "field_name": "env_local_scale",
      "value": "",
      "reason": "'whole-ecosystem treatment' (abstract) suggests a peatland, but no specific local feature is explicitly named."
    },
    {
      "id": "nmdc:bsm-11-abc123",
      "field_name": "env_medium",
      "value": "peat [ENVO:00005774]",
      "reason": "'peat warming' and 'surface peat' repeatedly cited in abstract."
    }
  ]
}
```
