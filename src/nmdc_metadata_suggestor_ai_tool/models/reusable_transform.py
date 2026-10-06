"""Pydantic model for a saved, reusable Metadata Mapper transform."""

from typing import Any

from pydantic import BaseModel, Field

from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import ColumnMapping


class ReusableTransform(BaseModel):
    """An approved column mapping, compiled to linkml-map files so it can be run again.

    On disk (see ``TransformLibrary``) this is three ordinary linkml-map-style files:
    ``source_schema.yaml`` (``source_schema``), ``transform.yaml`` (``spec``), and
    ``mapper.yaml`` (everything else: the approved mappings with their confidence and
    reasons, kept so the UI and the mapper agent can see why each column went where it did).
    """

    name: str = Field(description="Short identifier, also the folder name in a TransformLibrary")
    description: str = Field(default="", description="Where this transform came from")
    source_columns: list[str] = Field(
        description="Headers of the file the transform was built from, in file order"
    )
    mixs_extensions: list[str] = Field(default_factory=list)
    mappings: list[ColumnMapping] = Field(
        default_factory=list, description="Approved mappings the spec was compiled from"
    )
    source_schema: dict[str, Any] = Field(
        description="LinkML schema induced from the headers: one class, one string slot each"
    )
    spec: dict[str, Any] = Field(description="linkml-map TransformationSpecification")
    created: str | None = Field(default=None, description="ISO date the transform was saved")
