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

Do **not** fall back to all interfaces when uncertain — that dumps ~51 KB of value sets and blurs the answer. Infer the extension from sample fields (`env_package`, `depth`, `habitat`, `host_name`, `samp_name`); if it is still unclear, use the four extensions that have curated value sets — `SoilInterface`, `WaterInterface`, `SedimentInterface`, `PlantAssociatedInterface` — plus the biome list from Step 4.

The extension roster, which extensions have value sets, and the per-extension evidence hints are in [refs/extensions.md](refs/extensions.md).

---

## Step 4 — Load candidate pools

**Tier 1** — the curated value set, via the **schema-context** skill:

```python
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder

SchemaContextBuilder().format_env_triad_context(interfaces)
```

**Tier 2** — verified ENVO candidates, from the pinned index. Use `scripts/envo_lookup.py`; do not write Python against the index directly. It reads no network and takes no URL.

Run it from the repo root. Write the path out in full each time — do not stash it in a shell variable, which does not word-split under zsh.

```bash
# where to search for this extension
uv run python .claude/skills/env-triad/scripts/envo_lookup.py pool wastewater_sludge env_medium

# ranked candidates, scoped to the slot anchor and a subtree from `pool`
uv run python .claude/skills/env-triad/scripts/envo_lookup.py search "activated sludge" \
    --slot env_medium --within ENVO:00002044

# follow the train down to more specific terms
uv run python .claude/skills/env-triad/scripts/envo_lookup.py descendants ENVO:00002044

# the tier-3 value for this extension and slot
uv run python .claude/skills/env-triad/scripts/envo_lookup.py fallback wastewater_sludge env_medium
```

`search` matches whole words, tries the full phrase first, then falls back to individual words — so `"rhizosphere soil"` returns rhizosphere and soil terms rather than nothing. Scope it with `--slot` and `--within` to keep hits relevant.

For `env_broad_scale`, skip searching entirely: all of ENVO holds 127 biomes, so the `biomes` command is the complete universe for every extension. Load it once and pick from it.

Run `... envo_lookup.py --help` for the full verb list.

---

## Step 5 — Suggest values

Process samples in chunks of 50 if there are many. For each sample and each of the three slots, work down the tiers.

**Value rules:**
- Prefer a tier 1 value. Drop to tier 2 only when no curated value fits the evidence — then search the index rather than recalling a CURIE, and record `source: "envo_expansion"`
- "Follow the train down": start from the closest tier 1 term (or the extension's seed subtree) and run `descendants` on it, toward a more specific match the evidence supports
- If the exact term is already present in the sample record (e.g. an existing `env_broad_scale` field), copy it verbatim into `value` — after validating it
- If you quote a value in `reason`, that value is explicit — put it in `value` too
- Never leave `value` empty. With no defensible specific term, emit what `fallback <interface> <slot>` returns and record `source: "generalized"`

**Reason rules:**
- Every `reason` must cite specific input text (exact short quote up to 12 words, or paraphrase with a source label such as "abstract", "study description", "sample field")
- No generic domain justifications without citing input text
- Do not reference schema package names (e.g. `SoilInterface`) in reasons

---

## Step 6 — Validate before emitting

Run every value through the gate before emitting it. Pass all the values for one slot at once:

```bash
uv run python .claude/skills/env-triad/scripts/envo_lookup.py validate env_medium \
    "activated sludge [ENVO:00002046]" "sludge [ENVO:00002044]" --interface wastewater_sludge
```

Each value prints `PASS` or `FAIL`, its `source` tier, any failures or warnings, and a `use instead:` line when the label needs correcting. **The command exits non-zero if any value failed** — do not emit a value from a failing run. Drop a tier and try again.

Always pass `--interface`: allowed prefixes and value sets are per-interface, so validating without it is stricter than the schema actually is.

The gate checks the slot's declared pattern, that the term exists in ENVO and is not obsolete, that the label matches ENVO's official label, and anchor-class membership. Warnings are informational; only failures block a value. Take the tier label from the reported `source` rather than deciding it yourself. Full rules, the per-interface prefix table, and the known schema defects the gate is calibrated around are in [refs/validation.md](refs/validation.md).

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
