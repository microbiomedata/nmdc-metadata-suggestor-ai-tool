# MIxS extension reference

Twelve MIxS extensions carry env triad slots. Only four ship a curated value set, so for the other eight ENVO expansion (tier 2) is not a fallback — it is the only grounding available.

Portal names come from `HARMONIZER_TEMPLATES` in nmdc-server; `MixsExtensions.map_to_interface_name` accepts either column.

## Roster

Value set counts are `env_broad_scale`/`env_local_scale`/`env_medium`. Seed columns list the ENVO subtrees tier 2 searches within, with descendant counts.

| Portal name | Interface class | Value set | Local-scale seeds | Medium seeds |
|---|---|---|---|---|
| `soil` | `SoilInterface` | 52/83/85 | — | — |
| `water` | `WaterInterface` | 56/86/96 | — | — |
| `sediment` | `SedimentInterface` | 15/53/11 | — | — |
| `plant-associated` | `PlantAssociatedInterface` | 72/62/28 | — | — |
| `air` | `AirInterface` | none | — | `ENVO:00002005` air (3)<br>`ENVO:01000797` gaseous environmental material (24) |
| `built environment` | `BuiltEnvInterface` | none | `ENVO:00000073` building (115) | `ENVO:00002008` dust (19) |
| `microbial mat_biofilm` | `BiofilmInterface` | none | — | `ENVO:01000156` biofilm material (6) |
| `hydrocarbon resources-cores` | `HcrCoresInterface` | none | — | `ENVO:2000045` hydrocarbon-based environmental material (22)<br>`ENVO:00002984` petroleum (3) |
| `hydrocarbon resources-fluids_swabs` | `HcrFluidsSwabsInterface` | none | — | `ENVO:2000045` hydrocarbon-based environmental material (22)<br>`ENVO:00002984` petroleum (3)<br>`ENVO:00002985` oil (8) |
| `wastewater_sludge` | `WastewaterSludgeInterface` | none | — | `ENVO:00002044` sludge (11)<br>`ENVO:00002001` waste water (3)<br>`ENVO:00002018` sewage<br>`ENVO:00002059` biosolids |
| `host-associated` | `HostAssociatedInterface` | none | — | — |
| `miscellaneous natural or artificial environment` | `MiscEnvsInterface` | none | — | — |

A `—` in a seed column means no extension-specific subtree: search the slot's whole anchor class. That is deliberate for `host-associated` and `misc-envs`, whose sampled material is not confined to any one ENVO subtree.

`wastewater_sludge` is in the submission schema but has no portal template yet, so it arrives only via the ETL path.

Two extensions may go outside ENVO, because their slot patterns say so: `host-associated` admits UBERON on `env_local_scale` and `env_medium`, and `plant-associated` (with `soil` and `water`) admits PO on the same two slots. Those values validate on pattern alone — only ENVO is indexed — so prefer an ENVO term where one fits. See [validation.md](validation.md).

Seeds live in `EXPANSION_SEEDS` in `src/nmdc_metadata_suggestor_ai_tool/envo_index.py`. Every CURIE there is checked by `tests/test_envo_index.py` to exist, to be current, and to sit under its slot's anchor — add seeds by looking them up with `envo_lookup.py search`, never from memory.

## env_broad_scale needs no seeds

All of ENVO holds 127 biome terms (~5 KB). `envo_lookup.py biomes` is therefore the complete universe for this slot in *every* extension — no search step, no per-extension curation. The curated broad-scale value sets remain the preferred tier; the rest of the biome list is the labeled tier 2 fallback.

## Evidence hints by extension

Which sample fields carry the signal, once study context is in hand:

| Extension | Look at |
|---|---|
| soil, sediment | `depth`, `geo_loc_name`, `habitat`, horizon and land-use fields, core/profile language |
| water | `depth`, `lat_lon`, salinity and tidal language, `samp_name` naming a water body |
| plant-associated | `host_name`, `host_taxid`, plant part in `samp_name` — the medium is usually plant tissue, not soil |
| air | sampler and flow-rate fields, indoor/outdoor language |
| built environment | `build_occup_type`, `surf_material`, `indoor_space`, room and surface language |
| microbial mat_biofilm | the substrate the mat grows on — that is the local scale; the mat itself is the medium |
| hydrocarbon resources | reservoir, well, and production language; core versus fluid/swab distinguishes the two extensions |
| wastewater_sludge | treatment stage (influent, aeration basin, digester, effluent) |
| host-associated | `host_name`, `host_taxid`, body site |
| misc-envs | fall back to study context; this extension exists because nothing else fit |

## Future seam

`submission-schema/notebooks/environmental_context_value_sets/create_env_context_robot_template.py` targets ENVO-native NMDC subsets at `ENVO:03605010`–`ENVO:03605015`. Those IDs do not exist in ENVO 2026-06-26 or OLS4, so nothing here builds on them. If they land upstream, tiering can key off subset membership directly and the seed table becomes redundant.
