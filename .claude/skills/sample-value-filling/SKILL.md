---
name: sample-value-filling
description: Use this skill to suggest per-row (per-sample) values for NMDC MIxS metadata fields — any fillable slot except the env triad — from study context, DOI abstracts, PDFs, and the sample data itself. Returns row-addressed LLMOutput JSON ready to fill into DataHarmonizer cells.
---

# Sample Value Filling Skill

Suggest values for MIxS metadata fields **per sample row**, for any fillable slot the evidence
supports. You are the recommender — execute all steps yourself.

This is the general, row-level counterpart to `env-triad`. Each suggestion names one row and one
field, so `(id, field_name, value)` is a DataHarmonizer cell address: the row identified by `id`,
the column named `field_name`.

**IMPORTANT: Do NOT call StructuredOutput between steps. Complete every step first, then call
StructuredOutput exactly once at the end with the final `LLMOutput` JSON.**

---

## Division of labor — stay in your lane

Three skills fill values; keep them from suggesting the same field.

| Skill | Owns | `id` |
|---|---|---|
| `metadata-suggestion-pipeline` | Study / submission-level fields that describe the whole study | `null` |
| `env-triad` | `env_broad_scale`, `env_local_scale`, `env_medium` | per row |
| **`sample-value-filling` (this skill)** | **Every other fillable MIxS slot, per row** | **per row** |

Never emit `env_broad_scale`, `env_local_scale`, or `env_medium` — those belong to `env-triad`.
Never emit the GOLD ecosystem slots (`ecosystem`, `ecosystem_category`, `ecosystem_type`,
`ecosystem_subtype`, `specific_ecosystem`); the schema context below already excludes them.

---

## Step 1 — Assign sample IDs

Every suggestion must be tied to the row it fills. Copy the row's identifier **verbatim** — never
fabricate or normalize it:

1. If the row has an `id` field, use it.
2. Otherwise use `samp_name` — this is what DataHarmonizer shows as the row label, so it is the most
   reliable cell key.
3. If neither exists, assign the row's 0-based position within its `sampleData` group as a string
   (`"0"`, `"1"`, ...).

`sampleData` is a dict of `<extension>_data` lists (e.g. `water_data`, `soil_data`); each list holds
the row dicts. Keep track of which group each row came from — it tells you the interface in Step 3.

---

## Step 2 — Gather study context

**If a `submission_object` is provided:**
1. Use the **submission-parser** skill to extract structured fields.
2. Use the **build-study-context** skill to fetch DOI abstracts and PDFs.

**If `study_context` is provided (ETL path):** use the **build-study-context** skill with
`study_context` directly — no parsing needed.

**If neither is provided:** use only the sample data itself as evidence.

The result is `context_texts` (base text plus copyright-cleared abstracts) and `pdf_paths`. These,
plus the sample rows themselves, are your only evidence.

---

## Step 3 — Identify the MIxS extension(s)

Map each `sampleData` group to its interface class:

```python
from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import MixsExtensions

interfaces = MixsExtensions.map_to_interface_name(["water"])  # -> ["WaterInterface"]
```

`map_to_interface_name` accepts portal names (`"soil"`, `"water"`, `"microbial mat_biofilm"`) or
class names (`"SoilInterface"`). Use the `mixs_extensions` from the parsed submission when present;
otherwise infer from the `sampleData` group names and sample fields.

---

## Step 4 — Load the fillable slot context

Use the **schema-context** skill. The method for this skill keeps required slots (prime targets the
submitter still has to fill) and already drops the env triad and GOLD ecosystem slots:

```python
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder

SchemaContextBuilder().format_fillable_slots_context(interfaces)
```

Read the returned definitions carefully. For each slot they give the `type`, any `pattern`, any
`Allowed values` (a controlled vocabulary), whether it is `multivalued`, and `min`/`max`. These are
the constraints your values must satisfy — there is no validation gate downstream, so the only check
is the one you do here.

---

## Step 5 — Suggest values per row

Process rows in chunks of 50 if there are many. For each row, scan the evidence and the slot
definitions, and emit a suggestion for each slot the evidence supports. A field with no supporting
evidence for a row is simply **omitted** for that row — do not force a value, and do not invent one.

**Value rules:**
- **Explicit value:** when the source states that specific value for that sample (or for a group the
  sample clearly belongs to), copy it into `value`, formatted to match the slot's `type`/`pattern`.
- **Confident inference:** when the evidence supports a value for the row without stating it
  literally — for example the study describes a single collection site, so `geo_loc_name` applies to
  every row — you may fill `value` with that inference. Spell the inference out in `reason`. Only
  infer when the support is specific; a generic domain guess is not evidence.
- **Controlled vocabularies:** if a slot lists `Allowed values`, `value` must be one of them,
  copied verbatim. If none fits, omit the slot for that row.
- **Patterns and units:** match the slot's `pattern`. Many quantity slots expect a number followed by
  a unit (e.g. `"3.5 m"`, `"12 degrees Celsius"`); include the unit the source gives.
- **Multivalued slots:** return a list when the slot is `multivalued` and the evidence names several
  values.
- Never leave `value` empty on an emitted suggestion. If you cannot support a value, omit the
  suggestion entirely (this is the opposite of `env-triad`, which always fills).

**Reason rules:**
- Every `reason` must cite specific input text — an exact short quote (up to 12 words) or a
  paraphrase with a source label such as "abstract", "study description", "protocol description",
  "sample field". For an inference, name the evidence the inference rests on.
- No generic domain justifications without citing input text.
- Do not reference schema package or interface names (e.g. `WaterInterface`) in reasons.

---

## Output format

Return a JSON object matching `LLMOutput`. Emit one entry per `(row, supported field)` pair. Every
entry carries the row's `id`. Do **not** set `provenance` — this skill has no validation gate.

```json
{
  "metadata_fields": [
    {
      "id": "SAMP_001",
      "field_name": "collection_date",
      "value": "2021-07-14",
      "reason": "Methods state samples 1-10 were 'collected on 14 July 2021' (abstract)."
    },
    {
      "id": "SAMP_001",
      "field_name": "collection_time",
      "value": "08:30",
      "reason": "'sampled at 08:30 local time each morning' (protocol description)."
    },
    {
      "id": "SAMP_001",
      "field_name": "geo_loc_name",
      "value": "USA: Oregon, Crater Lake",
      "reason": "Study describes a single site, 'Crater Lake, Oregon'; applies to every row (inference)."
    },
    {
      "id": "SAMP_002",
      "field_name": "depth",
      "value": "10 m",
      "reason": "Sample table lists this sample at 'depth 10 m' (sample field)."
    }
  ]
}
```

Each `(id, field_name)` names a single DataHarmonizer cell: match `id` to the row's `samp_name` (or
its index when `samp_name` is absent), and `field_name` to the column.
