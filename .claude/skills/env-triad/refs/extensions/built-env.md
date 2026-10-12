# built environment

Constructed spaces and their surfaces.

Interface class `BuiltEnvInterface`. No curated value set — ENVO expansion is the only grounding available.

## Where candidates come from

This extension has expansion subtrees for `env_local_scale`, `env_medium`. Rather than relying on this file, print them with definitions
and descendant counts:

```python
from nmdc_metadata_suggestor_ai_tool.envo import get_envo

get_envo().format_expansion_context("BuiltEnvInterface", "env_local_scale")
```

## Evidence in the sample record

`build_occup_type`, `surf_material`, `indoor_space`, and room or surface language.

## Domain notes

> Not yet documented. See the contributor guide in `CONTRIBUTING.md`.

## Worked examples

> Not yet documented. See the contributor guide in `CONTRIBUTING.md`.
