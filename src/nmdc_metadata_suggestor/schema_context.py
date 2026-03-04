"""Schema context builder for NMDC submission schema.

Dynamically extracts relevant schema slices from the nmdc-submission-schema
package using LinkML's SchemaView, producing structured context suitable for
LLM prompts.
"""

from __future__ import annotations

import functools
import importlib.resources
from collections import defaultdict

from linkml_runtime.utils.schemaview import SchemaView  # type: ignore[import-untyped]

from nmdc_metadata_suggestor.constants import (
    ENV_TRIAD_SLOTS,
    EXCLUDED_INTERFACE_CLASSES,
    INTERFACE_CLASS_SUFFIX,
)
from nmdc_metadata_suggestor.models.schema import (
    DhInterface,
    EnumValueInfo,
    SlotInfo,
)


@functools.lru_cache(maxsize=1)
def get_schema_view() -> SchemaView:
    """Load and cache the NMDC submission schema as a SchemaView."""
    schema_ref = importlib.resources.files("nmdc_submission_schema.schema").joinpath(
        "nmdc_submission_schema.yaml"
    )
    with importlib.resources.as_file(schema_ref) as schema_path:
        return SchemaView(str(schema_path))


def format_slot(slot: SlotInfo) -> list[str]:
    """Format a single slot as Markdown lines for LLM context."""
    slot_lines: list[str] = []
    markers: list[str] = []
    if slot.required:
        markers.append("REQUIRED")
    if slot.recommended:
        markers.append("recommended")
    marker_str = f" [{', '.join(markers)}]" if markers else ""
    slot_lines.append(f"- **{slot.name}**{marker_str}")
    if slot.description:
        slot_lines.append(f"  {slot.description}")
    details: list[str] = []
    if slot.range:
        details.append(f"type: {slot.range}")
    if slot.multivalued:
        details.append("multivalued")
    if slot.pattern:
        details.append(f"pattern: `{slot.pattern}`")
    if slot.minimum_value is not None:
        details.append(f"min: {slot.minimum_value}")
    if slot.maximum_value is not None:
        details.append(f"max: {slot.maximum_value}")
    if details:
        slot_lines.append(f"  ({', '.join(details)})")
    if slot.enum_values:
        values_text = ", ".join(ev.text for ev in slot.enum_values)
        slot_lines.append(f"  Allowed values: {values_text}")
    return slot_lines


class SchemaContextBuilder:
    """Builds LLM-ready context from the NMDC submission schema."""

    def __init__(self, schema_view: SchemaView | None = None) -> None:
        self.sv = schema_view or get_schema_view()

    def list_interfaces(self) -> list[str]:
        """Return a sorted list of concrete interface class names."""
        return sorted(
            name
            for name in self.sv.all_classes()
            if name.endswith(INTERFACE_CLASS_SUFFIX) and name not in EXCLUDED_INTERFACE_CLASSES
        )

    def get_env_triad_enums(self, class_name: str) -> dict[str, list[EnumValueInfo]]:
        """Return env triad enum values for a given interface class.

        Uses SchemaView's ``induced_slot`` which propagates ``any_of`` from
        ``slot_usage``.  For each env triad slot, finds the first ``any_of``
        entry whose range is a known enum and resolves its permissible values.

        Returns a dict keyed by slot name with lists of ``EnumValueInfo``.
        Only slots that have an enum via ``any_of`` are included.
        """
        cls = self.sv.get_class(class_name)
        if cls is None:
            raise ValueError(f"Unknown class: {class_name}")

        result: dict[str, list[EnumValueInfo]] = {}
        for slot_name in ENV_TRIAD_SLOTS:
            enum_values = self.resolve_any_of_enum(self.sv.induced_slot(slot_name, class_name))
            if enum_values is not None:
                result[slot_name] = enum_values

        return result

    def resolve_any_of_enum(self, slot) -> list[EnumValueInfo] | None:  # noqa: ANN001
        """Extract enum values from the first enum-typed ``any_of`` entry on a slot."""
        if not slot.any_of:
            return None
        for item in slot.any_of:
            if item.range:
                enum_def = self.sv.get_enum(item.range)
                if enum_def is not None:
                    pvs = enum_def.permissible_values or {}
                    return [
                        EnumValueInfo(
                            text=str(pv.text),
                            description=str(pv.description) if pv.description else None,
                        )
                        for pv in pvs.values()
                    ]
        return None

    def get_interface_schema(self, class_name: str) -> DhInterface:
        """Extract full schema info for a single interface class."""
        cls = self.sv.get_class(class_name)
        if cls is None:
            raise ValueError(f"Unknown class: {class_name}")

        ancestors = [a for a in self.sv.class_ancestors(class_name) if a != class_name]
        induced_slots = self.sv.class_induced_slots(class_name)

        slots: list[SlotInfo] = []
        slot_groups: set[str] = set()
        required_count = 0
        recommended_count = 0

        for s in induced_slots:
            enum_values = None
            enum_total_count = None

            if s.range:
                enum_def = self.sv.get_enum(s.range)
                if enum_def is not None:
                    pvs = enum_def.permissible_values or {}
                    enum_total_count = len(pvs)
                    enum_values = [
                        EnumValueInfo(
                            text=str(pv.text),
                            description=str(pv.description) if pv.description else None,
                        )
                        for pv in pvs.values()
                    ]

            # Fall back to any_of for enum ranges (e.g. env triad slots)
            if enum_values is None:
                enum_values = self.resolve_any_of_enum(s)
                if enum_values is not None:
                    enum_total_count = len(enum_values)

            is_required = bool(s.required)
            is_recommended = bool(s.recommended)
            if is_required:
                required_count += 1
            if is_recommended:
                recommended_count += 1

            if s.slot_group:
                slot_groups.add(s.slot_group)

            slots.append(
                SlotInfo(
                    name=s.name,
                    description=str(s.description) if s.description else None,
                    range=s.range,
                    required=is_required,
                    recommended=is_recommended,
                    multivalued=bool(s.multivalued),
                    pattern=s.pattern,
                    slot_group=s.slot_group,
                    minimum_value=str(s.minimum_value) if s.minimum_value is not None else None,
                    maximum_value=str(s.maximum_value) if s.maximum_value is not None else None,
                    enum_values=enum_values,
                    enum_total_count=enum_total_count,
                )
            )

        return DhInterface(
            class_name=class_name,
            description=str(cls.description) if cls.description else None,
            ancestors=ancestors,
            slots=slots,
            slot_groups=sorted(slot_groups),
            total_slot_count=len(slots),
            required_slot_count=required_count,
            recommended_slot_count=recommended_count,
        )

    def format_interface_context(self, class_name: str) -> str:
        """Format schema info as structured Markdown for LLM context."""
        schema = self.get_interface_schema(class_name)
        lines: list[str] = [f"# {schema.class_name}"]
        if schema.description:
            lines.append(f"\n{schema.description}")
        lines.append(
            f"\nTotal fields: {schema.total_slot_count} | "
            f"Required: {schema.required_slot_count} | "
            f"Recommended: {schema.recommended_slot_count}"
        )
        if schema.ancestors:
            lines.append(f"Ancestors: {', '.join(schema.ancestors)}")

        # Group slots by slot_group
        grouped: dict[str, list[SlotInfo]] = defaultdict(list)
        ungrouped: list[SlotInfo] = []
        for slot in schema.slots:
            if slot.slot_group:
                grouped[slot.slot_group].append(slot)
            else:
                ungrouped.append(slot)

        for group_name in schema.slot_groups:
            if group_name in grouped:
                lines.append(f"\n## {group_name}")
                for slot in grouped[group_name]:
                    lines.extend(format_slot(slot))

        if ungrouped:
            lines.append("\n## Other fields")
            for slot in ungrouped:
                lines.extend(format_slot(slot))

        return "\n".join(lines)

    def format_multi_interface_context(self, class_names: list[str]) -> str:
        """Format multiple interfaces separated by dividers."""
        sections = [self.format_interface_context(name) for name in class_names]
        return "\n\n---\n\n".join(sections)
