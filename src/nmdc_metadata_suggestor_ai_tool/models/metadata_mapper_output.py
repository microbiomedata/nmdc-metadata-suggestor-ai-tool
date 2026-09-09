"""Pydantic models for the Metadata Mapper AI output."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SourceFile(BaseModel):
    """Identity record for one user-uploaded file."""

    file_id: str = Field(description="Resolvable file identifier")
    display_name: str = Field(description="Original filename shown in the UI")


class ValueConversion(BaseModel):
    """A transformation the AI recommends applying to raw column values."""
    # TODO - do we want to contrain the types of transformation?
    type: str = Field(
        description=(
            "Category of transformation. Known values: 'unit', 'date_format', 'split', 'none'. "
            "Other values are permitted as the LLM may identify novel conversion types."
        )
    )
    description: str = Field(description="Human-readable summary, e.g. 'MM/DD/YYYY → ISO 8601'")
    expression: str | None = Field(
        default=None,
        description="Machine-readable rule, e.g. a Python strptime format string or scale factor",
    )
    preview: list[dict[str, str]] = Field(
        default_factory=list,
        description="Sample input→output pairs, e.g. [{'input': '1024 ft', 'output': '312.12 m'}]",
    )


class ColumnMapping(BaseModel):
    """AI's suggested mapping for one user column to one NMDC slot."""

    source_column: str = Field(description="Column name from the uploaded file")
    source_file_id: str = Field(description="FK → SourceFile.file_id")
    mixs_extension: str | None = Field(
        default=None,
        description=(
            "MIxS extension this column belongs to (e.g. 'Air', 'Soil'). "
            "Validated against the schema. Null only when classification is impossible, "
            "which forces confidence='cant_place'."
        ),
    )
    nmdc_candidate_slots: list[str] = Field(
        default_factory=list,
        description="Ranked NMDC slot suggestions, best match first. Empty when confidence='cant_place'.",
    )
    confidence: Literal["high", "review", "cant_place"] = Field(
        description=(
            "'high' — clean mapping, no user action needed; "
            "'review' — candidate slot(s) identified but user must confirm; "
            "'cant_place' — no matching slot found, or mixs_extension is None"
        )
    )
    reason: str = Field(description="Why this confidence level was assigned")
    conversion: ValueConversion | None = Field(
        default=None,
        description="Value transformation required to conform to the NMDC slot, if any",
    )
    @model_validator(mode="after")
    def cant_place_if_no_env(self) -> "ColumnMapping":
        if self.mixs_extension is None and self.confidence != "cant_place":
            raise ValueError("mixs_extension=None requires confidence='cant_place'")
        return self


class MetadataMapperOutput(BaseModel):
    """Full AI mapping output for a user's uploaded dataset."""

    source_files: list[SourceFile] = Field(
        default_factory=list,
        description="One entry per uploaded file",
    )
    high_confidence: list[ColumnMapping] = Field(
        default_factory=list,
        description="Columns mapped cleanly; no user action required",
    )
    needs_review: list[ColumnMapping] = Field(
        default_factory=list,
        description="Columns with candidate mappings that the user must confirm",
    )
    cant_place: list[ColumnMapping] = Field(
        default_factory=list,
        description="Columns the AI could not map; user must resolve manually",
    )
    model: str | None = Field(default=None, description="LLM model used")
    access_provider: str | None = Field(default=None, description="LLM access provider")
