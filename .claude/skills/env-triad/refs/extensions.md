# MIxS extension reference

Twelve MIxS extensions carry env triad slots. Only four ship a curated value set, so for the other eight ENVO expansion is not a fallback — it is the only grounding available.

Portal names come from `HARMONIZER_TEMPLATES` in nmdc-server; `MixsExtensions.map_to_interface_name` accepts either of the first two columns.

## Roster

**Read only the file for the extension in play.** Each holds that extension's evidence signals and domain knowledge; loading all twelve wastes context on environments the samples are not from.

Value set counts are `env_broad_scale`/`env_local_scale`/`env_medium`.

| Portal name | Interface class | Value set | Reference |
|---|---|---|---|
| `plant-associated` | `PlantAssociatedInterface` | 72/62/28 | [extensions/plant-associated.md](extensions/plant-associated.md) |
| `sediment` | `SedimentInterface` | 15/53/11 | [extensions/sediment.md](extensions/sediment.md) |
| `soil` | `SoilInterface` | 52/83/85 | [extensions/soil.md](extensions/soil.md) |
| `water` | `WaterInterface` | 56/86/96 | [extensions/water.md](extensions/water.md) |
| `air` | `AirInterface` | none | [extensions/air.md](extensions/air.md) |
| `built environment` | `BuiltEnvInterface` | none | [extensions/built-env.md](extensions/built-env.md) |
| `host-associated` | `HostAssociatedInterface` | none | [extensions/host-associated.md](extensions/host-associated.md) |
| `hydrocarbon resources-cores` | `HcrCoresInterface` | none | [extensions/hcr-cores.md](extensions/hcr-cores.md) |
| `hydrocarbon resources-fluids_swabs` | `HcrFluidsSwabsInterface` | none | [extensions/hcr-fluids-swabs.md](extensions/hcr-fluids-swabs.md) |
| `microbial mat_biofilm` | `BiofilmInterface` | none | [extensions/biofilm.md](extensions/biofilm.md) |
| `miscellaneous natural or artificial environment` | `MiscEnvsInterface` | none | [extensions/misc-envs.md](extensions/misc-envs.md) |
| `wastewater_sludge` | `WastewaterSludgeInterface` | none | [extensions/wastewater-sludge.md](extensions/wastewater-sludge.md) |

File names come from the interface class, kebab-cased with `Interface` dropped, so the path is derivable rather than looked up.

`wastewater_sludge` is in the submission schema but has no portal template yet, so it arrives only via the ETL path.

## Rules that hold across every extension

### env_broad_scale needs no expansion subtrees

All of ENVO holds 127 biome terms (~5 KB), so `envo_lookup.py biomes` is the complete universe for this slot in *every* extension — no search step, no per-extension curation. The curated broad-scale value sets remain the preferred tier; the rest of the biome list is the labeled tier 2 fallback.

### Two extensions may go outside ENVO

Their slot patterns say so: `host-associated` admits UBERON on `env_local_scale` and `env_medium`, and `plant-associated` (with `soil` and `water`) admits PO on the same two slots. Those values validate on pattern alone — only ENVO is indexed — so prefer an ENVO term where one fits. See [validation.md](validation.md).

### Expansion seeds are looked up, never recalled

Seeds live in `EXPANSION_SEEDS` in `src/nmdc_metadata_suggestor_ai_tool/envo_index.py`, and `envo_lookup.py pool` prints them with definitions. Every CURIE there is checked by `tests/test_envo_index.py` to exist, to be current, and to sit under its slot's anchor. So is every CURIE written into these reference files — see `tests/test_env_triad_references.py`.

## Future seam

`submission-schema/notebooks/environmental_context_value_sets/create_env_context_robot_template.py` targets ENVO-native NMDC subsets at `ENVO:03605010`–`ENVO:03605015`. Those IDs do not exist in ENVO 2026-06-26 or OLS4, so nothing here builds on them. If they land upstream, tiering can key off subset membership directly and the seed tables become redundant.
