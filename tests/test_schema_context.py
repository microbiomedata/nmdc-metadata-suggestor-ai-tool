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


def test_get_env_triad_enums_soil_returns_all_three_slots() -> None:
    builder = SchemaContextBuilder()
    result = builder.get_env_triad_enums("SoilInterface")
    assert set(result.keys()) == {"env_broad_scale", "env_local_scale", "env_medium"}
    for values in result.values():
        assert len(values) > 0


def test_get_env_triad_enums_soil_counts() -> None:
    builder = SchemaContextBuilder()
    result = builder.get_env_triad_enums("SoilInterface")
    assert len(result["env_broad_scale"]) == 52
    assert len(result["env_local_scale"]) == 83
    assert len(result["env_medium"]) == 85


@pytest.mark.parametrize(
    "class_name",
    ["SoilInterface", "SedimentInterface", "WaterInterface", "PlantAssociatedInterface"],
)
def test_get_env_triad_enums_all_sample_types(class_name: str) -> None:
    builder = SchemaContextBuilder()
    result = builder.get_env_triad_enums(class_name)
    assert set(result.keys()) == {"env_broad_scale", "env_local_scale", "env_medium"}
    for values in result.values():
        assert len(values) > 0


def test_get_env_triad_enums_unknown_class_raises_value_error() -> None:
    builder = SchemaContextBuilder()
    with pytest.raises(ValueError, match="Unknown class"):
        builder.get_env_triad_enums("NonexistentInterface")


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


def test_format_multi_interface_context_separated() -> None:
    builder = SchemaContextBuilder()
    ctx = builder.format_multi_interface_context(["SoilInterface", "WaterInterface"])
    assert "# SoilInterface" in ctx
    assert "# WaterInterface" in ctx
    assert "---" in ctx
