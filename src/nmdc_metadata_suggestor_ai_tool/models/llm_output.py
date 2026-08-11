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
    source: str | None = Field(
        default=None,
        description=(
            "Where the value came from, for env triad suggestions: "
            "'submission_enum' (in the schema's curated value set for this MIxS "
            "extension), 'envo_expansion' (a verified ENVO term outside that set), "
            "or 'generalized' (broadest defensible term). Absent for other fields."
        ),
    )


class LLMOutput(BaseModel):
    """Top-level output payload returned by the LLM."""

    metadata_fields: list[MetadataFieldSuggestion] = Field(
        default_factory=list, description="List of metadata field suggestions"
    )
    model: str | None = Field(default=None, description="Name of the LLM model used")
    access_provider: str | None = Field(default=None, description="Access provider for the LLM")
