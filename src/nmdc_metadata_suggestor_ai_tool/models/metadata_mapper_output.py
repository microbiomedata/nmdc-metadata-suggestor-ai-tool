"""Pydantic models for the Metadata Mapper AI output."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SourceFile(BaseModel):
    """Identity record for one user-uploaded file."""

    file_id: str = Field(description="Resolvable file identifier")
    display_name: str = Field(description="Original filename shown in the UI")


class ValueConversion(BaseModel):
    """A transformation the AI recommends applying to raw column values."""

    type: str = Field(
        description=(
            "Category of transformation. Known values: "
            "'unit' — multiply by a numeric scale factor; "
            "'date_format' — reformat a date string; "
            "'split' — split on a delimiter and rejoin with '; '; "
            "'enum_map' — map source values to NMDC permissible values using a JSON object "
            "(expression must be a JSON object whose keys are source values and values are the "
            'canonical NMDC permissible values, e.g. \'{"Soil": "soil", "SOIL": "soil"}\'); '
            "'none' — pass through unchanged; "
            "'custom' — arbitrary Python expression where 'value' is the input string. "
            "Use 'enum_map' whenever the target slot has a fixed set of permissible values."
        )
    )
    description: str = Field(description="Human-readable summary, e.g. 'MM/DD/YYYY → ISO 8601'")
    expression: str | None = Field(
        default=None,
        description=(
            "REQUIRED when type is not 'none'. Machine-readable rule the executor will run. "
            "date_format: Python strptime format string for the SOURCE values, e.g. '%m/%d/%Y'. "
            "unit: decimal scale factor string, e.g. '0.3048' for feet→meters. "
            "split: delimiter string, e.g. ', '. "
            "custom: single Python expression where 'value' is the input string, "
            'e.g. "str(round((float(value) - 32) * 5 / 9, 2))" for Fahrenheit→Celsius. '
            "Never null when type is not 'none'."
        ),
    )
    # NOT populated by the LLM — excluded from the output JSON schema so the agent
    # never fills it. Populated after Phase 1 by build_conversion_previews() in
    # metadata_mapper/apply.py, which samples real CSV rows through ValueTransformer
    # so previews always reflect actual transformer output.
    preview: list[dict[str, str]] = Field(
        default_factory=list,
        exclude=True,
        description="Sample input→output pairs drawn from real data."
        "Set by build_conversion_previews(), not the LLM.",
    )

    @model_validator(mode="after")
    def expression_required_when_active(self) -> "ValueConversion":
        if self.type.lower() != "none" and self.expression is None:
            raise ValueError(
                f"ValueConversion.expression is required when type='{self.type}'. "
                "Provide the machine-readable rule (strptime format, scale factor, "
                "delimiter, or Python expression)."
            )
        return self


class ColumnMapping(BaseModel):
    """AI's suggested mapping for one user column to one NMDC slot."""

    source_column: str = Field(description="Column name from the uploaded file")
    combine_columns: list[str] = Field(
        default_factory=list,
        description=(
            "Additional source columns to merge with source_column into a single slot value. "
            "When non-empty, the conversion expression receives a dict named 'values' keyed by "
            "column name rather than a single 'value' string. Always requires type='custom' "
            "on the conversion."
        ),
    )
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
        description="Ranked NMDC slot suggestions, best match first."
        "Empty when confidence='cant_place'.",
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

    @model_validator(mode="after")
    def combine_requires_custom(self) -> "ColumnMapping":
        if self.combine_columns and self.conversion and self.conversion.type != "custom":
            raise ValueError(
                f"combine_columns requires conversion.type='custom', got '{self.conversion.type}'"
            )
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
