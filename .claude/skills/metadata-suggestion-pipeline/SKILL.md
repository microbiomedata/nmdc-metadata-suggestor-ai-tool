---
name: metadata-suggestion-pipeline
description: Use this skill to suggest metadata field values for a full NMDC submission object by parsing submission data, fetching DOI/PDF context, loading schema, and returning cited LLMOutput JSON.
---

# Metadata Suggestion Pipeline Skill

Use this skill to suggest metadata field values for a full NMDC submission object. You are the recommender — execute all steps yourself.

**IMPORTANT: Do NOT call StructuredOutput between steps. Complete ALL four steps first, then call StructuredOutput exactly once at the end with the final `LLMOutput` JSON.**

---

## Goal

Analyze the submission context and suggest values for any NMDC metadata fields that can be supported by the available evidence. Output only fields where there is specific, citable justification.

---

## Step 1 — Parse the submission object

Use the **submission-parser** skill to extract structured fields (DOIs, description, study name, protocol info, MIxS extensions).

---

## Step 2 — Build study context

Use the **build-study-context** skill with the parsed submission to:
- Assemble base text (description, notes, study name, protocol info)
- Fetch DOI abstracts
- Download PDFs

This yields `context_texts` (list of strings) and `pdf_paths` (list of temp file paths) to use as evidence.

---

## Step 3 — Load schema context

Use the **schema-context** skill with the `mixs_extensions` from the parsed submission:

```python
builder = SchemaContextBuilder()
mixs_schema = builder.format_multi_interface_context(mixs_extensions)
```

Use the returned slot definitions to understand what fields are available and what values are valid.

---

## Step 4 — Suggest metadata fields

Using all gathered evidence (context texts, PDFs, schema slots), suggest values for NMDC metadata fields. Apply these rules:

**Value rules:**
- Only populate `value` when the input explicitly contains the specific value
- If a value can be inferred but is not explicitly stated, put the inference in `reason` and leave `value` as `""`
- Suggested fields must exist in the NMDC schema — use only slot names from the schema context

**Reason rules:**
- Every `reason` must cite specific input text (exact short quote up to 12 words, or paraphrase with source label such as "abstract", "protocol description", "study name")
- No generic domain justifications without citing input text
- Do not reference schema package names (e.g. `SoilInterface`) in reasons
- Omit a field entirely if there is no strong, evidence-backed justification

**`id` field:** Leave as `null` for submission-level fields (not sample-specific).

---

## Output format

Return a JSON object matching `LLMOutput`:

```json
{
  "metadata_fields": [
    {
      "id": null,
      "field_name": "relevant_protocols",
      "value": "https://doi.org/10.17504/protocols.io.xxx",
      "reason": "Protocol DOI explicitly listed in multiOmicsForm.mbProtocols (protocol description)."
    },
    {
      "id": null,
      "field_name": "ecosystem_type",
      "value": "",
      "reason": "'peatland CO2, CH4 porewater production' (abstract) strongly implies a bog or fen ecosystem, but no exact NMDC term is explicitly named."
    }
  ]
}
```
