"""Pydantic models for LLM metadata recommendation output."""

from pydantic import BaseModel, Field


class MetadataFieldSuggestion(BaseModel):
    """A single metadata field recommendation from the LLM."""

    id: str | None = Field(
        None,
        description="Unique identifier if the sample record is associated with a specific input",
    )
    field_name: str = Field(description="Name of the metadata field")
    reason: str = Field(description="Explanation of why this value is recommended")
    value: (
        str
        | int
        | float
        | bool
        | list[str | int | float | bool]
        | dict[str, str | int | float | bool | list[str | int | float | bool]]
    ) = Field(default="", description="The recommended value for the metadata field")


class LLMOutput(BaseModel):
    """Top-level output payload returned by the LLM."""

    metadata_fields: list[MetadataFieldSuggestion] = Field(
        default_factory=list, description="List of metadata field suggestions"
    )
    model: str | None = Field(None, description="Name of the LLM model used")
    access_provider: str | None = Field(None, description="Access provider for the LLM")
