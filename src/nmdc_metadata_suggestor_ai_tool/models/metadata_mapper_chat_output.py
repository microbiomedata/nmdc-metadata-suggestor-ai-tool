"""Pydantic models for the Metadata Mapper AI assistant sidebar output."""

from pydantic import BaseModel, Field


class SuggestedQuestion(BaseModel):
    """A follow-up question the assistant surfaces to the user."""

    text: str = Field(description="Question text shown as a clickable chip in the UI")


class MetadataMapperChatOutput(BaseModel):
    """Response from the AI assistant panel during the mapping review step."""

    message: str = Field(description="Assistant reply text")
    column_context: str | None = Field(
        default=None,
        description="The source column this response is scoped to, if any",
    )
    related_slots: list[str] = Field(
        default_factory=list,
        description="NMDC slots referenced or recommended in the reply",
    )
    suggested_questions: list[SuggestedQuestion] = Field(
        default_factory=list,
        description="Follow-up questions surfaced as chips below the reply",
    )
