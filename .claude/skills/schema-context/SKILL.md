---
name: schema-context
description: Use this skill to retrieve and format NMDC submission schema context (MIxS interface slots and env triad values) for use in LLM recommendation prompts.
---

# Schema Context Skill

Use this skill when you need to retrieve or format NMDC submission schema context for LLM prompts.

```python
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder

builder = SchemaContextBuilder()
```

**For full metadata suggestions** — call `format_multi_interface_context(mixs_extensions)` with interface names from the parsed submission (e.g. `["SoilInterface"]`).

**For env triad only** — call `format_env_triad_context(class_names)`, which returns only the `env_broad_scale`, `env_local_scale`, and `env_medium` slot definitions.

For the full method reference and slot filtering rules, see [refs/methods.md](refs/methods.md).
