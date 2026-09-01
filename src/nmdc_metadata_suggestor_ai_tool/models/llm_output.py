"""Pydantic models for LLM metadata recommendation output."""

from typing import Literal

from pydantic import BaseModel, Field

TriadTier = Literal["submission_enum", "envo_expansion", "generalized"]


class TriadProvenance(BaseModel):
    """How one env triad value was arrived at.

    Written by ``enforce_env_triad_values`` and never by the model: the tier is a
    derived fact about the value, not a claim the model makes about itself.
    """

    tier: TriadTier = Field(
        description=(
            "Which pool the value came from: 'submission_enum' (in a curated value set), "
            "'envo_expansion' (a verified ENVO term outside any curated set), or "
            "'generalized' (the slot's broadest defensible term)."
        )
    )
    outcome: Literal["accepted", "repaired", "replaced"] = Field(
        description=(
            "What the gate did: 'accepted' left the model's value alone, 'repaired' "
            "rewrote it (e.g. a label/CURIE mismatch), 'replaced' discarded it for the "
            "generic fallback. See original_value for the two latter cases."
        )
    )
    interface: str | None = Field(
        default=None,
        description=(
            "The MIxS extension whose curated value set justified a 'submission_enum' "
            "tier. Null when no value set was involved."
        ),
    )
    scoped: bool = Field(
        default=False,
        description=(
            "True when the caller named the extension. False means the gate scanned every "
            "extension and settled on `interface`, so a 'submission_enum' tier says the "
            "value is curated somewhere, not necessarily for this sample's extension."
        ),
    )
    original_value: str | None = Field(
        default=None,
        description="What the model proposed, when the gate repaired or replaced it.",
    )


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
    provenance: "TriadProvenance | None" = Field(
        default=None,
        description=(
            "How an env triad value was arrived at. Populated by the validation gate, "
            "never by the model. Absent for other fields."
        ),
    )


class LLMOutput(BaseModel):
    """Top-level output payload returned by the LLM."""

    metadata_fields: list[MetadataFieldSuggestion] = Field(
        default_factory=list, description="List of metadata field suggestions"
    )
    model: str | None = Field(default=None, description="Name of the LLM model used")
    access_provider: str | None = Field(default=None, description="Access provider for the LLM")
