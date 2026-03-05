"""Schema-related models for NMDC submission schema context building."""

from pydantic import BaseModel, Field


class EnumValueInfo(BaseModel):
    """A single permissible value from a LinkML enum."""

    text: str
    description: str | None = None


class SlotInfo(BaseModel):
    """Metadata for a single slot (field) in an NMDC submission."""

    name: str
    description: str | None = None
    range: str | None = None
    required: bool = False
    recommended: bool = False
    multivalued: bool = False
    pattern: str | None = None
    slot_group: str | None = None
    minimum_value: str | None = None
    maximum_value: str | None = None
    enum_values: list[EnumValueInfo] | None = Field(
        default=None,
        description=("Permissible values when the range is an enum"),
    )
    enum_total_count: int | None = Field(
        default=None,
        description="Total number of enum values",
    )


class InterfaceSchemaClass(BaseModel):
    """Extracted schema information for one NMDC submission interface class."""

    class_name: str
    description: str | None = None
    ancestors: list[str] = Field(default_factory=list)
    slots: list[SlotInfo] = Field(default_factory=list)
    slot_groups: list[str] = Field(default_factory=list)
    total_slot_count: int = 0
    required_slot_count: int = 0
    recommended_slot_count: int = 0
