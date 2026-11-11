# Schema Context Method Reference

**Import path:** `nmdc_metadata_suggestor_ai_tool.schema_context`

```python
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder

builder = SchemaContextBuilder()
```

The constructor optionally accepts a pre-loaded `SchemaView`. Without one, it calls `get_schema_view()`, which loads and caches `nmdc_submission_schema.yaml` automatically.

---

## Methods

| Method | When to use |
|---|---|
| `format_multi_interface_context(class_names)` | Format multiple interfaces separated by `---` dividers. Primary method for full metadata suggestions. |
| `format_env_triad_context(class_names)` | Convenience wrapper returning only `env_broad_scale`, `env_local_scale`, and `env_medium` slots. |
| `format_fillable_slots_context(class_names)` | Every per-row slot a general value filler may populate, divider-separated. Keeps required slots (marked `[required]`); drops deprecated, `EXCLUDED_SLOTS`, and the env triad. Primary method for `sample-value-filling`. |
| `required_fillable_slots(class_name)` | Names of the required slots in the fillable set (same exclusions), in schema order. |
| `missing_required_slots(class_name, row)` | The required fillable slots one sample row has no value for (absent, `None`, blank string, or empty list). |
| `format_interface_context(class_name)` | Format a single interface as structured Markdown ready for an LLM prompt. |
| `format_specific_slots_context(class_name, slot_names)` | Format only a named subset of slots from one interface. |
| `list_interfaces()` | Enumerate all valid interface class names (classes ending in `"Interface"`, minus excluded ones). |
| `get_interface_schema(class_name)` | Get the full `InterfaceSchemaClass` object for one interface (slots, ancestors, counts, etc.). |
| `filter_slots(schema)` | Strip excluded/required/deprecated slots. Called internally by `format_interface_context`. |

---

## Slot filtering rules

`filter_slots` removes slots that are:
- In `EXCLUDED_SLOTS`: `ecosystem`, `ecosystem_category`, `ecosystem_type`, `ecosystem_subtype`, `specific_ecosystem`, `collection_date_inc`
- Marked `required` or `deprecated`

The fillable methods (`format_fillable_*`, `required_fillable_slots`, `missing_required_slots`) do **not** use `filter_slots`: they keep required slots and drop the env triad instead.

Excluded interface classes (never appear in `list_interfaces`):
`DhInterface`, `JgiMgInterface`, `JgiMgLrInterface`, `JgiMtInterface`, `EmslInterface`
