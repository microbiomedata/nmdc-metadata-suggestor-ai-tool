# wastewater_sludge

Wastewater and sludge from treatment processes.

Interface class `WastewaterSludgeInterface`. No curated value set — ENVO expansion is the only grounding available.

## Where candidates come from

This extension has expansion subtrees for `env_medium`. Rather than relying on this file, print them with definitions
and descendant counts:

```python
from nmdc_metadata_suggestor_ai_tool.envo_index import get_envo_index

get_envo_index().format_expansion_context("WastewaterSludgeInterface", "env_medium")
```

## Evidence in the sample record

Treatment stage — influent, aeration basin, digester, effluent.

## Domain notes

> Not yet documented. See the contributor guide in `CONTRIBUTING.md`.

## Worked examples

> Not yet documented. See the contributor guide in `CONTRIBUTING.md`.
