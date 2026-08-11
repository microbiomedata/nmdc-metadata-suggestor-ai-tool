# microbial mat_biofilm

Microbial mats and biofilms.

Interface class `BiofilmInterface`. No curated value set — ENVO expansion is the only grounding available.

## Where candidates come from

This extension has expansion subtrees for `env_medium`. Rather than relying on this file, print them with definitions
and descendant counts:

```python
from nmdc_metadata_suggestor_ai_tool.envo_index import get_envo_index

get_envo_index().format_expansion_context("BiofilmInterface", "env_medium")
```

## Evidence in the sample record

The substrate the mat grows on — that is the local scale; the mat itself is the medium.

## Domain notes

> Not yet documented. See the contributor guide in `CONTRIBUTING.md`.

## Worked examples

> Not yet documented. See the contributor guide in `CONTRIBUTING.md`.
