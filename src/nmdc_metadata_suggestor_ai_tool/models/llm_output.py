"""Pydantic models for LLM metadata recommendation output."""

from pydantic import BaseModel, Field


class MetadataFieldSuggestion(BaseModel):
    """A single metadata field recommendation from the LLM."""

    field_name: str
    reason: str
    # Value can be several concrete JSON types. Use concrete unions so the
    # generated JSON Schema contains explicit `type` entries for arrays
    # and object properties (avoids `items` without a `type`).
    value: (
        str
        | int
        | float
        | bool
        | list[str | int | float | bool]
        | dict[str, str | int | float | bool | list[str | int | float | bool]]
    ) = ""


class LLMOutput(BaseModel):
    """Top-level output payload returned by the LLM."""

    metadata_fields: list[MetadataFieldSuggestion] = Field(default_factory=list)
    model: str | None = None
    access_provider: str | None = None
