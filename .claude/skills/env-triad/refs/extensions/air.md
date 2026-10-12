# air

Air and airborne material.

Interface class `AirInterface`. No curated value set — ENVO expansion is the only grounding available.

## Where candidates come from

This extension has expansion subtrees for `env_medium`. Rather than relying on this file, print them with definitions
and descendant counts:

```python
from nmdc_metadata_suggestor_ai_tool.envo import get_envo

get_envo().format_expansion_context("AirInterface", "env_medium")
```

## Evidence in the sample record

Sampler and flow-rate fields, and indoor versus outdoor language.

## Domain notes

> Not yet documented. See the contributor guide in `CONTRIBUTING.md`.

## Worked examples

> Not yet documented. See the contributor guide in `CONTRIBUTING.md`.
