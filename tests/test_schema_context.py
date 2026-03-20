"""Tests for schema_context module.

Uses the real installed nmdc-submission-schema package — no mocking needed
since it's a local, deterministic, no-network-call dependency.
"""

import pytest

from nmdc_metadata_suggestor_ai_tool.constants import EXCLUDED_INTERFACE_CLASSES, EXCLUDED_SLOTS
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder, get_schema_view


def test_get_schema_view_loads_and_returns_schema_view() -> None:
    sv = get_schema_view()
    assert sv is not None
    assert len(sv.all_classes()) > 0


def test_list_interfaces_excludes_interfaces() -> None:
    builder = SchemaContextBuilder()
    interfaces = builder.list_interfaces()
    for excluded in EXCLUDED_INTERFACE_CLASSES:
        assert excluded not in interfaces


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
    """Extracts permissible values for a single
    slot (e.g., env_medium) from a specific interface and format them as
    context for the next step.
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
    # Verify the prompt fragment
    assert "env_medium" in prompt_fragment
    assert "permissible values" in prompt_fragment
    assert len(terms) == env_medium.enum_total_count


def test_extracts_relevant_schema() -> None:
    """Extract only the relevant slice of the schema based on a user's submission context.

    Scenario:
        A user is filling out a SoilInterface submission.  Narrow
        the full schema down to just the slots, enums, and
        constraints relevant to that interface.
    """
    interface_name = "SoilInterface"
    builder = SchemaContextBuilder()

    # Use SchemaView to inspect class-level metadata ---------------
    cls = builder.sv.get_class(interface_name)
    assert cls is not None
    assert cls.description is not None

    # Use class_induced_slots() to get *only* this interface's slots
    induced = list(builder.sv.class_induced_slots(interface_name))
    env_broad = next(s for s in induced if s.name == "env_broad_scale")
    # Direct range lookup returns None for any_of-based enums…
    assert builder.sv.get_enum(env_broad.range) is None
    enum_values = builder.resolve_any_of_enum(env_broad)
    assert enum_values is not None
    assert len(enum_values) > 0
    assert all(ev.text for ev in enum_values)

    # Build a focused LLM prompt using only the relevant slice -----
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


def test_excluded_slots_not_in_interfaces() -> None:
    """Verify that excluded slots are not present in any interface schemas."""
    builder = SchemaContextBuilder()
    interfaces = builder.list_interfaces()
    slot_names = set()
    for interface in interfaces:
        schema = builder.get_interface_schema(interface)
        for slot in schema.slots:
            slot_names.add(slot.name)

    for excluded in EXCLUDED_SLOTS:
        assert excluded not in slot_names, f"{excluded} should be excluded from schemas"
