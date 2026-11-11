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

**For per-row value filling (`sample-value-filling`)** — call `format_fillable_slots_context(class_names)`, which keeps required slots and marks them `[required]` (the full-suggestion method above drops them). Then call `missing_required_slots(class_name, row)` for each row to get the required slots that row still lacks; each must be filled or flagged as required.

**For env triad only** — call `format_env_triad_context(class_names)`, which returns only the `env_broad_scale`, `env_local_scale`, and `env_medium` slot definitions.

For the full method reference and slot filtering rules, see [refs/methods.md](refs/methods.md).
