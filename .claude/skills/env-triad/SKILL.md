---
name: env-triad
description: Use this skill to suggest env_broad_scale, env_local_scale, and env_medium values for NMDC biosample records using study context, DOI abstracts, schema enumerations, and verified ENVO terms.
---

# Env Triad Recommendation Skill

Suggest `env_broad_scale`, `env_local_scale`, and `env_medium` for one or more NMDC biosample records. You are the recommender — execute all steps yourself.

Values use the format `"label [ENVO:NNNNNNN]"`. Resolve in three tiers, preferring the earliest that fits:

| Tier | `source` | Where the value comes from |
|---|---|---|
| 1 | `submission_enum` | The curated value set for this MIxS extension in the NMDC submission schema |
| 2 | `envo_expansion` | A verified ENVO term outside that set — used when tier 1 has nothing appropriate, or when the extension ships no value set at all (8 of 12 do not) |
| 3 | `generalized` | The broadest defensible term for the extension. Never leave `value` empty |

Every value passes **Step 6 validation** before you emit it.

---

## Step 1 — Assign sample IDs

For sample level suggestions, the suggestion must be tied to an ID. Copy the ID verbatim if it exists in the input data. If samples are missing an `id` field, assign a string index (`"0"`, `"1"`, ...). Never fabricate or normalize existing IDs — copy them verbatim.

---

## Step 2 — Gather study context

**If a `submission_object` is provided:**
1. Use the **submission-parser** skill to extract structured fields
2. Use the **build-study-context** skill to fetch DOI abstracts and PDFs

**If `study_context` is provided (ETL path):** use the **build-study-context** skill with `study_context` directly — no parsing needed.

**If neither is provided:** use only the sample data itself as evidence.

---

## Step 3 — Identify the MIxS extension

Determine which environment type(s) apply from the samples and context, then map them to interface class names:

```python
from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import MixsExtensions

interfaces = MixsExtensions.map_to_interface_name(["soil"])  # -> ["SoilInterface"]
```

`map_to_interface_name` accepts portal names (`"soil"`, `"microbial mat_biofilm"`) or class names (`"SoilInterface"`), and warns on anything it does not recognize.

Do **not** fall back to all interfaces when uncertain — that loads ~51 KB of value sets and reduces precision. Infer the extension from sample fields (`env_package`, `depth`, `habitat`, `host_name`, `samp_name`); if it is still unclear, use the four extensions that have curated value sets — `SoilInterface`, `WaterInterface`, `SedimentInterface`, `PlantAssociatedInterface` — plus the biome list from Step 4.

Then read the reference for the extension you settled on. [refs/extensions.md](refs/extensions.md) holds the roster, the rules that hold across all twelve, and a link per extension; `refs/extensions/<name>.md` holds that extension's evidence signals, domain knowledge, and worked examples. Read only the ones for the extensions in play — the others describe environments your samples are not from.

---

## Step 4 — Load candidate pools

**Tier 1** — the curated value set, via the **schema-context** skill:

```python
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder

SchemaContextBuilder().format_env_triad_context(interfaces)
```

**Tier 2** — verified ENVO candidates, looked up in ENVO through oaklib. Never recall a CURIE; every one you use must come back from a lookup.

```python
from nmdc_metadata_suggestor_ai_tool.envo import get_envo

envo = get_envo()
envo.format_expansion_context("WastewaterSludgeInterface", "env_medium")  # where to search
envo.search("activated sludge", slot="env_medium", within=("ENVO:00002044",))
envo.descendants("ENVO:00002044")  # specialize downward to more specific terms
envo.generic_fallback("WastewaterSludgeInterface", "env_medium")  # the tier-3 value
```

`search` matches whole words, tries the full phrase first, then falls back to individual words — so `"rhizosphere soil"` returns rhizosphere and soil terms rather than nothing. Scope it with `slot=` (the slot's anchor class) and `within=` (a subtree from `format_expansion_context`) to keep hits relevant.

For `env_broad_scale`, skip searching entirely: all of ENVO holds 127 biomes, so `envo.biome_values()` is the complete universe for every extension. Load it once and pick from it.

---

## Step 5 — Suggest values

Process samples in chunks of 50 if there are many. For each sample and each of the three slots, work down the tiers.

Before choosing terms, know what the three slots actually ask for — a biome, a countable
thing nearby, and a mass noun. [refs/slots.md](refs/slots.md) has ENVO's own guidance,
including the anti-patterns it names.

**Values are copied, never assembled.** Take the whole `label [ENVO:NNNNNNN]` string
verbatim — from the value set, or from `term.value` on a term you looked up. Never pair a
label you wrote yourself with a CURIE, and never take the label from one value-set entry and
the CURIE from another. Both halves can name real terms while the pair names neither, and the
value set cannot save you: it is a flat list, so adjacent entries look interchangeable.

**Value rules:**
- Prefer a tier 1 value. Drop to tier 2 only when no curated value fits the evidence — then search the index rather than recalling a CURIE, and record `source: "envo_expansion"`
- Specialize downward: start from the closest tier 1 term (or the extension's seed subtree) and call `descendants` on it, looking for a more specific match the evidence supports
- If the exact term is already present in the sample record (e.g. an existing `env_broad_scale` field), copy it verbatim into `value` — after validating it
- If you quote a value in `reason`, that value is explicit — put it in `value` too
- Never leave `value` empty. With no defensible specific term, emit `envo.generic_fallback(interface, slot)` and record `source: "generalized"`

**Reason rules:**
- Every `reason` must cite specific input text (exact short quote up to 12 words, or paraphrase with a source label such as "abstract", "study description", "sample field")
- No generic domain justifications without citing input text
- Do not reference schema package names (e.g. `SoilInterface`) in reasons

---

## Step 6 — Validate before emitting

Check every value before emitting it. A value that fails does not get emitted — drop a tier and try again.

**The descent is bounded at three steps, because tier 3 cannot fail.** `envo.generic_fallback(interface, slot)` returns a value for every extension and slot, and a test asserts every one of them validates. So a value that fails at tier 2 falls to tier 3 and stops there — never keep retrying.

For `env_local_scale` that floor is the bare anchor class, which carries almost no information. Reaching it is a prompt to re-read the evidence for a specific feature, not a usable answer.

```python
result = envo.validate(
    "activated sludge [ENVO:00002046]", "env_medium", "WastewaterSludgeInterface"
)
result.ok, result.failures, result.warnings, result.corrected_value, result.source
```

Always pass the interface name: allowed prefixes and value sets are per-interface, so validating without it is stricter than the schema actually is.

The gate checks the slot's declared pattern, that the term exists in ENVO and is not obsolete, that the label matches ENVO's official label, and anchor-class membership. Warnings are informational; only `failures` block a value. Take the tier label from `result.source` rather than deciding it yourself. Full rules, the per-interface prefix table, and the known schema defects the gate is calibrated around are in [refs/validation.md](refs/validation.md).

The same gate runs again in Python on whatever you return, so a value that slips through here is repaired or replaced rather than reaching the caller. That is a backstop, not a substitute — a value it has to replace loses the evidence you gathered and falls back to a generic term.

---

## Output format

Return a JSON object matching `LLMOutput`. Emit **three entries per sample** (one per env triad field) — so N samples produces 3N entries total. Every entry must carry the sample's `id` and its `source` tier.

```json
{
  "metadata_fields": [
    {
      "id": "nmdc:bsm-11-abc123",
      "field_name": "env_broad_scale",
      "value": "subpolar coniferous forest biome [ENVO:01000250]",
      "reason": "Study description identifies a 'boreal peatland' site, which maps to the subpolar coniferous forest biome.",
      "source": "submission_enum"
    },
    {
      "id": "nmdc:bsm-11-abc123",
      "field_name": "env_local_scale",
      "value": "peatland [ENVO:00000044]",
      "reason": "Study-level description explicitly names a 'peatland'.",
      "source": "submission_enum"
    },
    {
      "id": "nmdc:bsm-11-abc123",
      "field_name": "env_medium",
      "value": "histosol [ENVO:00002243]",
      "reason": "Sample collection notes reference a 'peat core'; peat is organic soil material that classifies as histosol.",
      "source": "submission_enum"
    },
    {
      "id": "nmdc:bsm-11-def456",
      "field_name": "env_medium",
      "value": "activated sludge [ENVO:00002046]",
      "reason": "Sample description names an 'aeration basin' of a treatment plant.",
      "source": "envo_expansion"
    }
  ]
}
```
