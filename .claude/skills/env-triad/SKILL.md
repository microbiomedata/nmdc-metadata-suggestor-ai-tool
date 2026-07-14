---
name: env-triad
description: Use this skill to suggest env_broad_scale, env_local_scale, and env_medium values for NMDC biosample records using study context, DOI abstracts, and schema enumerations.
---

# Env Triad Recommendation Skill

Use this skill to suggest values for `env_broad_scale`, `env_local_scale`, and `env_medium` for one or more NMDC biosample records. You are the recommender — execute all steps yourself.

---

## Goal

For each input sample, you must produce three metadata field suggestions:
- `env_broad_scale` — broad ecological biome (e.g. `"temperate grassland biome [ENVO:01000193]"`)
- `env_local_scale` — local environmental feature (e.g. `"agricultural field [ENVO:00000114]"`)
- `env_medium` — environmental material (e.g. `"soil [ENVO:00001998]"`)

Values must use the format `"label [CURIE]"` from the NMDC schema enumerations.

---

## Step 1 — Assign sample IDs
For sample level suggestions, the suggestion must be tied to an ID. Copy the ID verbatiem if it exists in the input data. If samples are missing an `id` field, assign a string index (`"0"`, `"1"`, ...). Never fabricate or normalize existing IDs — copy them verbatim.

---

## Step 2 — Gather study context

**If a `submission_object` is provided:**
1. Use the **submission-parser** skill to extract structured fields
2. Use the **build-study-context** skill to fetch DOI abstracts and PDFs

**If `study_context` is provided (ETL path):**
1. Use the **build-study-context** skill with `study_context` directly — no parsing needed

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

## Step 3b — Map environment names to interface class names

Call `MixsExtensions.map_to_interface_name(mixs_extensions_strings)` with the list of environment name strings identified in Step 3 to get the corresponding interface class names required by Step 4.

The full mapping is:

| Environment name (Step 3 value) | Interface class name |
|---|---|
| `soil` | `SoilInterface` |
| `water` | `WaterInterface` |
| `air` | `AirInterface` |
| `sediment` | `SedimentInterface` |
| `built environment` | `BuiltEnvInterface` |
| `host-associated` | `HostAssociatedInterface` |
| `microbial mat_biofilm` | `BiofilmInterface` |
| `plant-associated` | `PlantAssociatedInterface` |
| `hydrocarbon resources-cores` | `HcrCoresInterface` |
| `hydrocarbon resources-fluids_swabs` | `HcrFluidsSwabsInterface` |
| `miscellaneous natural or artifical environment` | `MiscEnvsInterface` |

Any unrecognized environment name should be skipped with a warning.

---

## Step 4 — Load schema context

Use the **schema-context** skill: call `SchemaContextBuilder().format_env_triad_context(class_names)` with the interface class names produced in Step 3b (e.g. `["SoilInterface"]`) to get the allowed values and definitions for the three env triad slots.

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

Return a JSON object matching `LLMOutput`. Emit **three entries per sample** (one per env triad field) — so N samples produces 3N entries total. Every entry must carry the sample's `id`.

```json
{
  "metadata_fields": [
    {
      "id": "nmdc:bsm-11-abc123",
      "field_name": "env_broad_scale",
      "value": "subpolar coniferous forest biome [ENVO:01000250]",
      "reason": "Study describes a boreal peatland under a warming treatment; the picklist biome for boreal/taiga peatlands is subpolar coniferous forest biome."
    },
    {
      "id": "nmdc:bsm-11-abc123",
      "field_name": "env_local_scale",
      "value": "peatland [ENVO:00000044]",
      "reason": "Study description says 'peatland' but this sample names no more specific local feature, so the general picklist term peatland is used."
    },
    {
      "id": "nmdc:bsm-11-abc123",
      "field_name": "env_medium",
      "value": "peat soil [ENVO:00005774]",
      "reason": "Abstract cites 'surface peat' and 'peat warming'; peat soil is the specific material, more precise than the picklist term histosol."
    },
    {
      "id": "nmdc:bsm-11-def456",
      "field_name": "env_broad_scale",
      "value": "subpolar coniferous forest biome [ENVO:01000250]",
      "reason": "Same study and site as the sample above, so the same boreal biome applies."
    },
    {
      "id": "nmdc:bsm-11-def456",
      "field_name": "env_local_scale",
      "value": "sphagnum bog [ENVO:00002268]",
      "reason": "Sample description names a 'Sphagnum bog', which is a more specific ENVO term than the picklist term peatland, so the exact term is used."
    },
    {
      "id": "nmdc:bsm-11-def456",
      "field_name": "env_medium",
      "value": "peat soil [ENVO:00005774]",
      "reason": "Sample collection notes cite a 'peat core'; peat soil is the specific material, more precise than the picklist term histosol."
    }
  ]
}
```
