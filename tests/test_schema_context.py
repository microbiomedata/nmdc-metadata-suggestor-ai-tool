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


def test_extract_slot_enum_values_for_llm_context(capsys) -> None:
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

    # Step 1: Find the slot by name
    env_medium = next(s for s in schema.slots if s.name == "env_medium")
    assert len(env_medium.enum_values) > 0

    # Step 3: Build an LLM-ready prompt fragment from the permissible values
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

    # Print so developers can see the shape of the output with pytest -s
    print("\n--- Example LLM prompt fragment for env_medium (SoilInterface) ---")
    print(prompt_fragment[:500])
    print(f"... ({len(terms)} total terms)")


def test_format_multi_interface_context_separated() -> None:
    builder = SchemaContextBuilder()
    ctx = builder.format_multi_interface_context(["SoilInterface", "WaterInterface"])
    assert "# SoilInterface" in ctx
    assert "# WaterInterface" in ctx
    assert "---" in ctx
