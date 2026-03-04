"""Tests for schema_context module.

Uses the real installed nmdc-submission-schema package — no mocking needed
since it's a local, deterministic, no-network-call dependency.
"""

import pytest

from nmdc_metadata_suggestor.schema_context import SchemaContextBuilder, _get_schema_view


class TestGetSchemaView:
    def test_loads_and_returns_schema_view(self) -> None:
        sv = _get_schema_view()
        assert sv is not None
        assert len(sv.all_classes()) > 0

    def test_caches_result(self) -> None:
        sv1 = _get_schema_view()
        sv2 = _get_schema_view()
        assert sv1 is sv2


class TestListInterfaces:
    def test_returns_sorted_list(self) -> None:
        builder = SchemaContextBuilder()
        interfaces = builder.list_interfaces()
        assert interfaces == sorted(interfaces)

    def test_excludes_dh_interface(self) -> None:
        builder = SchemaContextBuilder()
        interfaces = builder.list_interfaces()
        assert "DhInterface" not in interfaces

    def test_all_end_with_interface(self) -> None:
        builder = SchemaContextBuilder()
        interfaces = builder.list_interfaces()
        assert all(name.endswith("Interface") for name in interfaces)

    def test_contains_known_interfaces(self) -> None:
        builder = SchemaContextBuilder()
        interfaces = builder.list_interfaces()
        assert "SoilInterface" in interfaces
        assert "WaterInterface" in interfaces
        assert "AirInterface" in interfaces


class TestGetInterfaceSchema:
    def test_soil_interface_has_slots(self) -> None:
        builder = SchemaContextBuilder()
        schema = builder.get_interface_schema("SoilInterface")
        assert schema.class_name == "SoilInterface"
        assert schema.total_slot_count > 0
        assert len(schema.slots) == schema.total_slot_count

    def test_soil_interface_has_required_slots(self) -> None:
        builder = SchemaContextBuilder()
        schema = builder.get_interface_schema("SoilInterface")
        assert schema.required_slot_count > 0

    def test_soil_interface_has_slot_groups(self) -> None:
        builder = SchemaContextBuilder()
        schema = builder.get_interface_schema("SoilInterface")
        assert len(schema.slot_groups) > 0

    def test_soil_interface_has_ancestors(self) -> None:
        builder = SchemaContextBuilder()
        schema = builder.get_interface_schema("SoilInterface")
        assert "DhInterface" in schema.ancestors

    def test_samp_name_is_required(self) -> None:
        builder = SchemaContextBuilder()
        schema = builder.get_interface_schema("SoilInterface")
        samp_name = next(s for s in schema.slots if s.name == "samp_name")
        assert samp_name.required is True
        assert samp_name.description is not None

    def test_unknown_class_raises_value_error(self) -> None:
        builder = SchemaContextBuilder()
        with pytest.raises(ValueError, match="Unknown class"):
            builder.get_interface_schema("NonexistentInterface")

    def test_enum_values_extracted(self) -> None:
        builder = SchemaContextBuilder()
        schema = builder.get_interface_schema("SoilInterface")
        # Find a slot with enum values
        enum_slots = [s for s in schema.slots if s.enum_values is not None]
        assert len(enum_slots) > 0
        slot = enum_slots[0]
        assert len(slot.enum_values) > 0  # type: ignore[arg-type]
        assert slot.enum_total_count == len(slot.enum_values)  # no truncation

    def test_all_enum_values_included(self) -> None:
        """Verify no truncation — all enum values are returned."""
        builder = SchemaContextBuilder()
        schema = builder.get_interface_schema("SoilInterface")
        for slot in schema.slots:
            if slot.enum_values is not None:
                assert slot.enum_total_count == len(slot.enum_values)


class TestFormatInterfaceContext:
    def test_contains_class_name(self) -> None:
        builder = SchemaContextBuilder()
        ctx = builder.format_interface_context("SoilInterface")
        assert "# SoilInterface" in ctx

    def test_contains_required_marker(self) -> None:
        builder = SchemaContextBuilder()
        ctx = builder.format_interface_context("SoilInterface")
        assert "REQUIRED" in ctx

    def test_contains_slot_names(self) -> None:
        builder = SchemaContextBuilder()
        ctx = builder.format_interface_context("SoilInterface")
        assert "samp_name" in ctx

    def test_contains_section_headers(self) -> None:
        builder = SchemaContextBuilder()
        ctx = builder.format_interface_context("SoilInterface")
        # Should have at least one ## section header for slot groups
        assert "## " in ctx

    def test_contains_enum_values(self) -> None:
        builder = SchemaContextBuilder()
        ctx = builder.format_interface_context("SoilInterface")
        assert "Allowed values:" in ctx


class TestFormatMultiInterfaceContext:
    def test_multiple_interfaces_separated(self) -> None:
        builder = SchemaContextBuilder()
        ctx = builder.format_multi_interface_context(["SoilInterface", "WaterInterface"])
        assert "# SoilInterface" in ctx
        assert "# WaterInterface" in ctx
        assert "---" in ctx
