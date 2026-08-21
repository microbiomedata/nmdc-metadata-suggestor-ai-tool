# hydrocarbon resources-cores

Solid core material from hydrocarbon reservoirs.

Interface class `HcrCoresInterface`. No curated value set — ENVO expansion is the only grounding available.

## Where candidates come from

This extension has expansion subtrees for `env_medium`. Rather than relying on this file, print them with definitions
and descendant counts:

```python
from nmdc_metadata_suggestor_ai_tool.envo import get_envo

get_envo().format_expansion_context("HcrCoresInterface", "env_medium")
```

## Evidence in the sample record

Reservoir, well, and production language. Core material distinguishes this from the fluids and swabs extension.

## Domain notes

> Not yet documented. See the contributor guide in `CONTRIBUTING.md`.

## Worked examples

> Not yet documented. See the contributor guide in `CONTRIBUTING.md`.
