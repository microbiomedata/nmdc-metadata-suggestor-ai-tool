"""Pydantic models for LLM metadata recommendation output."""

from typing import Literal

from pydantic import BaseModel, Field, SerializeAsAny

TriadTier = Literal["submission_enum", "envo_expansion", "generalized"]


class BaseProvenance(BaseModel):
    """Base class for all provenance records.

    Subclass this for each validation gate that can modify a suggestion's value.
    The ``gate`` discriminator identifies which gate wrote the record.
    """

    gate: str = Field(description="Which validation gate wrote this provenance record.")


class TriadProvenance(BaseProvenance):
    """How one env triad value was arrived at.

    Written by ``enforce_env_triad_values`` and never by the model: the tier is a
    derived fact about the value, not a claim the model makes about itself.
    """

    gate: str = "env_triad"
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
            "The MIxS extension that determined the tier: whose curated value set holds "
            "the value, or whose generic fallback it is. Null when neither applies."
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
        default=None,
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
    # SerializeAsAny keeps the subclass's own fields. Annotated as the base, pydantic
    # serializes to the base -- a TriadProvenance would leave the process as {"gate":
    # "env_triad"} with tier, outcome, interface, scoped and original_value dropped.
    provenance: SerializeAsAny[BaseProvenance] | None = Field(
        default=None,
        description=(
            "How this value was arrived at. Populated by a validation gate, never by the model. "
            "Absent for fields with no gate."
        ),
    )


class LLMOutput(BaseModel):
    """Top-level output payload returned by the LLM."""

    metadata_fields: list[MetadataFieldSuggestion] = Field(
        default_factory=list, description="List of metadata field suggestions"
    )
    model: str | None = Field(default=None, description="Name of the LLM model used")
    access_provider: str | None = Field(default=None, description="Access provider for the LLM")
