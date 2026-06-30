---
name: schema_context
description: Use this skill to retrieve and format NMDC submission schema context (MIxS interface slots and env triad values) for use in LLM recommendation prompts.
---

# Schema Context Skill

Use this skill when you need to retrieve or format NMDC submission schema context for LLM prompts, as done in the recommendation pipeline.

## Key class: `SchemaContextBuilder`

**Import path:** `nmdc_metadata_suggestor_ai_tool.schema_context`

```python
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder

builder = SchemaContextBuilder()
```

The constructor optionally accepts a pre-loaded `SchemaView`. Without one, it calls `get_schema_view()`, which loads and caches the `nmdc_submission_schema.yaml` automatically.

---

## Getting schema context

Schema context is retrieved from a list of MIxS interface names and formatted for the LLM:

```python
# mixs_extensions is a list[str] of interface class names, e.g. ["SoilInterface", "WaterInterface"]
mixs_extensions = parsed_submission_object.get("mixs_extensions", [])

builder = SchemaContextBuilder()
mixs_schema = builder.format_multi_interface_context(mixs_extensions)

# Pass the result to the conversation manager
conversation_manager.add_schema_context(mixs_schema)
```

---

## Available methods

| Method | When to use |
|---|---|
| `list_interfaces()` | Enumerate all valid interface class names (classes ending in `"Interface"`, minus excluded ones). |
| `get_interface_schema(class_name)` | Get the full `InterfaceSchemaClass` object for one interface (slots, ancestors, counts, etc.). |
| `filter_slots(schema)` | Strip excluded/required/deprecated slots from an `InterfaceSchemaClass`. Called internally by `format_interface_context`. |
| `format_interface_context(class_name)` | Format a single interface as structured Markdown ready for an LLM prompt. |
| `format_multi_interface_context(class_names)` | Format multiple interfaces separated by `---` dividers. |
| `format_specific_slots_context(class_name, slot_names)` | Format only a named subset of slots from one interface. |
| `format_env_triad_context(class_names)` | Convenience wrapper that calls `format_specific_slots_context` for `env_broad_scale`, `env_local_scale`, and `env_medium`. |

---

## Slot filtering rules

`filter_slots` removes slots that are:
- In `EXCLUDED_SLOTS` (`ecosystem`, `ecosystem_category`, `ecosystem_type`, `ecosystem_subtype`, `specific_ecosystem`, `collection_date_inc`)
- Marked `required` or `deprecated`

Excluded interface classes (never appear in `list_interfaces`):
`DhInterface`, `JgiMgInterface`, `JgiMgLrInterface`, `JgiMtInterface`, `EmslInterface`

---

## Example: get context for a single known interface

```python
builder = SchemaContextBuilder()
markdown_context = builder.format_interface_context("SoilInterface")
print(markdown_context)
```

## Example: get env triad context across multiple interfaces

```python
builder = SchemaContextBuilder()
env_context = builder.format_env_triad_context(["SoilInterface", "WaterInterface"])
```
