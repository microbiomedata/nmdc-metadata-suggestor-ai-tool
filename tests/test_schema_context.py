"""Tests for schema_context module.

Uses the real installed nmdc-submission-schema package — no mocking needed
since it's a local, deterministic, no-network-call dependency.
"""

import pytest

from nmdc_metadata_suggestor.schema_context import SchemaContextBuilder, get_schema_view


def test_get_schema_view_loads_and_returns_schema_view() -> None:
    sv = get_schema_view()
    assert sv is not None
    assert len(sv.all_classes()) > 0


def test_list_interfaces_excludes_dh_interface() -> None:
    builder = SchemaContextBuilder()
    interfaces = builder.list_interfaces()
    assert "DhInterface" not in interfaces


def test_list_interfaces_all_end_with_interface() -> None:
    builder = SchemaContextBuilder()
    interfaces = builder.list_interfaces()
    assert all(name.endswith("Interface") for name in interfaces)


def test_list_interfaces_contains_known_interfaces() -> None:
    builder = SchemaContextBuilder()
    interfaces = builder.list_interfaces()
    assert "SoilInterface" in interfaces
    assert "WaterInterface" in interfaces
    assert "AirInterface" in interfaces


def test_get_interface_schema_soil_has_ancestors() -> None:
    builder = SchemaContextBuilder()
    schema = builder.get_interface_schema("SoilInterface")
    assert "DhInterface" in schema.ancestors


def test_get_interface_schema_samp_name_is_required() -> None:
    builder = SchemaContextBuilder()
    schema = builder.get_interface_schema("SoilInterface")
    samp_name = next(s for s in schema.slots if s.name == "samp_name")
    assert samp_name.required is True
    assert samp_name.description is not None


def test_get_interface_schema_unknown_class_raises_value_error() -> None:
    builder = SchemaContextBuilder()
    with pytest.raises(ValueError, match="Unknown class"):
        builder.get_interface_schema("NonexistentInterface")


def test_get_interface_schema_all_enum_values_included() -> None:
    """Verify all enum values are returned without truncation."""
    builder = SchemaContextBuilder()
    schema = builder.get_interface_schema("SoilInterface")
    for slot in schema.slots:
        if slot.enum_values is not None:
            assert slot.enum_total_count == len(slot.enum_values)


# TODO: expand/refine these as the middleware develops.
def test_format_interface_context_contains_class_name() -> None:
    builder = SchemaContextBuilder()
    ctx = builder.format_interface_context("SoilInterface")
    assert "# SoilInterface" in ctx


def test_format_interface_context_contains_required_marker() -> None:
    builder = SchemaContextBuilder()
    ctx = builder.format_interface_context("SoilInterface")
    assert "REQUIRED" in ctx


def test_get_interface_schema_populates_env_triad_enum_values() -> None:
    """Verify get_interface_schema populates enum_values for env triad slots."""
    builder = SchemaContextBuilder()
    schema = builder.get_interface_schema("SoilInterface")
    env_triad_names = {"env_broad_scale", "env_local_scale", "env_medium"}
    for slot in schema.slots:
        if slot.name in env_triad_names:
            assert slot.enum_values is not None, f"{slot.name} should have enum_values"
            assert slot.enum_total_count is not None
            assert slot.enum_total_count == len(slot.enum_values)
            assert slot.enum_total_count > 0


def test_extract_slot_enum_values_for_llm_context() -> None:
    """Demonstrate how a developer extracts permissible values for a single
    slot (e.g., env_medium) from a specific interface and formats them as
    context suitable for passing to an LLM.

    Usage pattern:
        1. Get the full interface schema via get_interface_schema()
        2. Find the slot of interest
        3. Read its enum_values — these are already resolved from any_of
        4. Format as a prompt fragment for the LLM
    """
    builder = SchemaContextBuilder()
    schema = builder.get_interface_schema("SoilInterface")
    env_medium = next(s for s in schema.slots if s.name == "env_medium")
    assert len(env_medium.enum_values) > 0
    terms = [
        f"- {ev.text}: {ev.description}" if ev.description else f"- {ev.text}"
        for ev in env_medium.enum_values
    ]
    prompt_fragment = (
        f"The field '{env_medium.name}' accepts {len(terms)} permissible values "
        f"from the NMDC submission schema for SoilInterface.\n"
        f"Select the most appropriate value given the sample context:\n" + "\n".join(terms)
    )
    # Verify the prompt fragment is well-formed
    assert "env_medium" in prompt_fragment
    assert "permissible values" in prompt_fragment
    assert len(terms) == env_medium.enum_total_count


def test_schemaview_dynamically_extracts_relevant_schema_slice() -> None:
    """Demonstrate how SchemaView queries let us dynamically extract only
    the relevant slice of the schema based on a user's submission context.

    Scenario:
        A user is filling out a SoilInterface submission.  We use SchemaView
        to narrow the full schema down to just the slots, enums, and
        constraints relevant to that interface — without loading or
        transmitting the entire schema to the LLM.

    Key branch additions exercised:
        - ``get_schema_view()`` is now public, giving callers direct
          SchemaView access for ad-hoc queries.
        - ``builder.sv`` is public, so downstream code can combine
          high-level helpers with raw SchemaView calls.
        - ``resolve_any_of_enum()`` handles ``any_of``-style enum
          ranges (e.g. the environmental triad slots).
    """
    interface_name = "SoilInterface"
    builder = SchemaContextBuilder()

    # --- 1. Use SchemaView to inspect class-level metadata ---------------
    cls = builder.sv.get_class(interface_name)
    assert cls is not None
    assert cls.description is not None

    # --- 2. Use class_induced_slots() to get *only* this interface's slots
    #     rather than iterating the entire schema --------------------------
    induced = list(builder.sv.class_induced_slots(interface_name))
    all_slot_count = len(list(builder.sv.all_slots()))
    assert len(induced) > 0
    assert len(induced) < all_slot_count  # we got a focused slice, not everything

    # --- 3. Dynamically resolve enums for a slot whose range uses any_of --
    env_broad = next(s for s in induced if s.name == "env_broad_scale")
    # Direct range lookup returns None for any_of-based enums…
    assert builder.sv.get_enum(env_broad.range) is None
    # …but resolve_any_of_enum() (new on this branch) finds them:
    enum_values = builder.resolve_any_of_enum(env_broad)
    assert enum_values is not None
    assert len(enum_values) > 0
    assert all(ev.text for ev in enum_values)

    # --- 4. Build a focused LLM prompt using only the relevant slice -----
    schema = builder.get_interface_schema(interface_name)
    required_slots = [s for s in schema.slots if s.required]
    enum_slots = [s for s in schema.slots if s.enum_values]

    prompt_lines = [
        f"# Schema context for {interface_name}",
        f"{schema.description}",
        "",
        f"## Required fields ({len(required_slots)} of {schema.total_slot_count})",
    ]
    for slot in required_slots:
        constraint = f" (type: {slot.range})" if slot.range else ""
        prompt_lines.append(f"- {slot.name}{constraint}: {slot.description or ''}")

    prompt_lines.append("")
    prompt_lines.append(f"## Fields with controlled vocabularies ({len(enum_slots)})")
    for slot in enum_slots:
        prompt_lines.append(f"- {slot.name}: {slot.enum_total_count} allowed values")

    prompt = "\n".join(prompt_lines)

    # Verify the prompt is a focused, well-formed slice — not the whole schema
    assert interface_name in prompt
    assert "Required fields" in prompt
    assert "controlled vocabularies" in prompt
    assert len(required_slots) == schema.required_slot_count
    assert len(required_slots) < schema.total_slot_count
    assert len(enum_slots) > 0
    # env triad slots should appear among enum-bearing slots
    enum_slot_names = {s.name for s in enum_slots}
    assert "env_broad_scale" in enum_slot_names
    assert "env_local_scale" in enum_slot_names
    assert "env_medium" in enum_slot_names


def test_format_multi_interface_context_separated() -> None:
    builder = SchemaContextBuilder()
    ctx = builder.format_multi_interface_context(["SoilInterface", "WaterInterface"])
    assert "# SoilInterface" in ctx
    assert "# WaterInterface" in ctx
    assert "---" in ctx
