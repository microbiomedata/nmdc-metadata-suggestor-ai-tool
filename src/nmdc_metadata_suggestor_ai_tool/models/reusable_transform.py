"""Pydantic model for a saved, reusable Metadata Mapper transform."""

from typing import Any

from pydantic import BaseModel, Field

from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import ColumnMapping


class ReusableTransform(BaseModel):
    """An approved column mapping, compiled to a linkml-map spec so it can be run again.

    The ``mappings`` are what a reviewer approved (confidence, reason, conversion), kept so
    the UI and the mapper agent can see why each column went where it did. The ``spec`` is a
    linkml-map TransformationSpecification compiled from those mappings; it is what actually
    runs against CSV rows.
    """

    name: str = Field(description="Short identifier, also the file stem in a TransformLibrary")
    description: str = Field(default="", description="Where this transform came from")
    source_columns: list[str] = Field(
        description="Headers of the file the transform was built from, in file order"
    )
    column_slots: dict[str, str] = Field(
        description=(
            "Original header → slot name in the induced source schema. Headers are not always "
            "valid identifiers, and linkml-map expressions need identifiers."
        )
    )
    mixs_extensions: list[str] = Field(default_factory=list)
    mappings: list[ColumnMapping] = Field(
        default_factory=list, description="Approved mappings the spec was compiled from"
    )
    spec: dict[str, Any] = Field(description="linkml-map TransformationSpecification")
    created: str | None = Field(default=None, description="ISO date the transform was saved")
