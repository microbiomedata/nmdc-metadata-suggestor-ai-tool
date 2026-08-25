# Env triad validation

```python
from nmdc_metadata_suggestor_ai_tool.envo import get_envo

result = get_envo().validate(
    "activated sludge [ENVO:00002046]", "env_medium", "WastewaterSludgeInterface"
)
```

`ValidationResult` carries `ok`, `failures`, `warnings`, `term`, `corrected_value`, `in_valueset`, and `source`. Only `failures` block a value; warnings are informational.

## The same gate runs again in Python

`enforce_env_triad_values` applies these checks to every `LLMOutput`, on both the programmatic and the agentic path, so a bad value cannot reach a caller just because this step was skipped. It corrects label drift in place and replaces a hard failure with the extension's generic fallback.

That is a backstop, not a substitute. It has no access to the evidence you gathered, so a value it has to replace becomes a generic term with its reasoning stripped down to a note. Validate here and the suggestion survives intact.

## The checks

| # | Check | Fails when |
|---|---|---|
| 1 | Slot name | Not one of the three env triad slots |
| 2 | Value pattern | The value does not match the pattern the schema declares **for this interface and slot** — see prefixes below |
| 3 | Term exists | The ENVO CURIE is absent from the ENVO release oaklib is serving |
| 4 | Not obsolete | ENVO has deprecated the term |
| 5 | Label matches | The label differs from ENVO's official label. `corrected_value` carries the fix — use it rather than re-deriving |
| 6 | Anchor class | The term does not descend from the slot's MIxS anchor |

Anchors: `env_broad_scale` → biome `ENVO:00000428`; `env_local_scale` → astronomical body part `ENVO:01000813`; `env_medium` → environmental material `ENVO:00010483`.

## Allowed prefixes are not uniform

The schema declares three different patterns across the 36 env triad slots. Pass `interface_name` to `validate` so it reads the right one; without it the strictest (ENVO-only) applies.

| Prefixes | Slots |
|---|---|
| ENVO only | 28 slots — everything not listed below |
| ENVO or **UBERON** | `HostAssociatedInterface` `env_local_scale`, `env_medium` |
| ENVO or **PO** | `PlantAssociatedInterface`, `SoilInterface`, `WaterInterface` — `env_local_scale`, `env_medium` |

So host anatomy is legitimately available for host-associated samples, and plant anatomy for plant, soil, and water samples. Only ENVO is indexed, so a UBERON or PO value passes the pattern and then returns a warning that it could not be checked further. Prefer an ENVO term when one fits, because it can be verified.

## Two gates are deliberately soft

**Check 6 is a hard gate only for `env_broad_scale` and `env_medium`.** For `env_local_scale` it is a warning: the local-scale value sets carry 13–16 terms per package that do not sit under the anchor (`aquifer`, `farm`, `fen`, `biofilm`, `litter layer`). Gating there would reject values NMDC's own schema allows.

**Check 6 never applies to a value already in the interface's curated set.** The anchor gate polices ENVO expansion, not the schema. `rhizosphere [ENVO:00005801]`, for instance, is a curated plant-associated `env_medium` that ENVO classifies as a system rather than a material. Checks 3 and 4 still apply everywhere, so dead CURIEs are caught even inside a curated set.

## When the label and the CURIE disagree

They can disagree in three ways, and the gate treats them differently, because the CURIE is
not automatically the trustworthy half.

| The label | Outcome |
|---|---|
| is a synonym of the CURIE's term | normalised to ENVO's official label |
| names no ENVO term (`dirt`) | drift on a sound CURIE — corrected to the official label |
| names a *different* real term | **repaired toward the label**, if that term suits the slot |
| names several terms, or one that fails the slot | rejected; neither half can be trusted |

The third row is the one worth understanding. `temperate coniferous forest biome
[ENVO:01000219]` pairs a label whose CURIE is `ENVO:01000211` with the CURIE for
`anthropogenic terrestrial biome` — two separate entries in the same value set. Trusting the
CURIE would silently commit a different biome from the one the `reason` argues for, so the
label wins: it is what was reasoned about and what a reviewer reads.

A wrong CURIE under a right label is therefore recoverable. A wrong label is not.

## On failure

Drop a tier and try again — do not emit a failing value.

| Failure | Do this |
|---|---|
| Label mismatch | Emit `result.corrected_value` |
| Term missing or obsolete | Search the index for a current term covering the same concept |
| Wrong anchor | You have the right concept at the wrong grain. A biome proposed for `env_medium` means you named the setting, not the material — search the material anchor instead |
| Pattern | The prefix is not allowed on this slot. Find the ENVO equivalent, or fall to tier 3 |

If nothing survives, emit `envo.generic_fallback(interface, slot)` with `source: "generalized"`. Never emit an empty `value`.

The fallback is the broadest term in the extension's curated set when that set has a root (`soil [ENVO:00001998]`, `aquatic biome [ENVO:00002030]`), otherwise the first expansion seed (`air [ENVO:00002005]`, `sludge [ENVO:00002044]`). For **`env_local_scale` it is always the bare anchor** — no value set has a genuine broadest member, they are flat collections of sibling features. A `generalized` local scale therefore carries almost no information: treat it as a prompt to re-examine the evidence rather than a usable answer.

`result.in_valueset` tells you which tier a value actually came from, and `result.source` turns that into the `submission_enum` / `envo_expansion` label — use it rather than deciding the tier yourself.

## Coherence

Beyond the mechanical checks: `env_local_scale` should be finer-grained than `env_broad_scale`, and `env_medium` must name a material (a mass noun — soil, water, sludge) rather than a place or a countable thing.

## Known schema defects the gate is calibrated around

Verified against nmdc-submission-schema and ENVO 2026-06-26.

- **`ENVO:02000139`** (desert spring) and **`ENVO:02000145`** (subterranean lake) appear in the water value sets and no longer exist in ENVO or OLS4. The gate rejects them; do not emit them even though the schema offers them. Worth reporting upstream.
- Aside from those two, no curated value in any of the four value sets is rejected — `tests/test_envo.py` asserts it, so a schema bump that breaks the assumption fails CI rather than silently dropping suggestions.

## Which ENVO release is in play

Ontology access goes through oaklib's `sqlite:obo:envo` adapter, which downloads and caches ENVO on
first use. `envo.envo_version` reports the release, and every "not in ENVO" failure quotes it, so a
rejection can always be traced to a specific release.

Nothing about ENVO is asserted from memory anywhere in this skill. Every CURIE in the code and in
these reference files is checked against that release on every test run.
