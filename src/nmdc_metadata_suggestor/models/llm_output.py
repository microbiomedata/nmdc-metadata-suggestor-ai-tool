"""Pydantic models for LLM metadata recommendation output."""

from pydantic import BaseModel, Field


class MetadataFieldSuggestion(BaseModel):
    """A single metadata field recommendation from the LLM."""

    field_name: str
    reason: str
    value: str | list | dict = ""  # Value can be of any type depending on the field, but default to empty string if not provided


class LLMOutput(BaseModel):
    """Top-level output payload returned by the LLM."""

    metadata_fields: list[MetadataFieldSuggestion] = Field(default_factory=list)
    model: str | None = None
    access_provider: str | None = None
