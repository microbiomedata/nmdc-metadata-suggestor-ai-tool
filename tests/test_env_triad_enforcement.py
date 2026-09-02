"""Tests for the env triad pipeline's ENVO context and value enforcement.

These cover the non-agentic path in env_triad_recommendation: the ENVO candidate
context handed to the model, and the gate that repairs or replaces what comes
back. Unit tests for the index itself live in test_envo.py.
"""

import json
from typing import Any

from nmdc_metadata_suggestor_ai_tool.env_triad_recommendation import (
    build_envo_expansion_context,
    enforce_env_triad_values,
)
from nmdc_metadata_suggestor_ai_tool.models.llm_output import (
    LLMOutput,
    MetadataFieldSuggestion,
    TriadProvenance,
    TriadTier,
)


def one_suggestion(value: Any, slot: str = "env_medium") -> LLMOutput:
    return LLMOutput(
        metadata_fields=[MetadataFieldSuggestion(field_name=slot, reason="r", value=value)]
    )


def tier_of(suggestion: MetadataFieldSuggestion) -> TriadTier:
    """The tier the gate assigned, asserting it assigned one at all."""
    assert isinstance(suggestion.provenance, TriadProvenance)
    return suggestion.provenance.tier


def provenance_of(output: LLMOutput, index: int = 0) -> TriadProvenance:
    provenance = output.metadata_fields[index].provenance
    assert isinstance(provenance, TriadProvenance)
    return provenance


# --- ENVO context -----------------------------------------------------------


def test_expansion_context_always_carries_the_biome_list() -> None:
    context = build_envo_expansion_context(None)
    assert "every ENVO biome" in context
    assert "marine biome [ENVO:00000447]" in context


def test_unknown_interfaces_still_get_expansion_candidates() -> None:
    """None means every extension here, matching what the schema context does with it.

    Eight of the twelve extensions ship no curated value set, so ENVO expansion is their
    only grounding. Reading None as "no extensions" left the prompt carrying biomes and
    nothing else, while still telling the model to use only supplied CURIEs.
    """
    context = build_envo_expansion_context(None)
    assert "sludge [ENVO:00002044]" in context
    assert "air [ENVO:00002005]" in context


def test_expansion_context_names_the_extension_subtrees() -> None:
    context = build_envo_expansion_context(["WastewaterSludgeInterface"])
    assert "sludge [ENVO:00002044]" in context
    assert "Generic fallback" in context


# --- enforcement ------------------------------------------------------------


def test_enforce_repairs_label_drift() -> None:
    output = enforce_env_triad_values(one_suggestion("dirt [ENVO:00001998]"), ["SoilInterface"])
    suggestion = output.metadata_fields[0]
    assert suggestion.value == "soil [ENVO:00001998]"
    assert isinstance(suggestion.provenance, TriadProvenance)
    assert tier_of(suggestion) == "submission_enum"
    assert suggestion.provenance.outcome == "repaired"
    assert suggestion.provenance.original_value == "dirt [ENVO:00001998]"
    assert suggestion.provenance.interface == "SoilInterface"
    assert suggestion.provenance.scoped is True


def test_enforce_derives_the_tier_from_validation() -> None:
    """The tier is derived from the value, not reported by the model."""
    output = enforce_env_triad_values(
        one_suggestion("activated sludge [ENVO:00002046]"),
        ["WastewaterSludgeInterface"],
    )
    suggestion = output.metadata_fields[0]
    assert suggestion.value == "activated sludge [ENVO:00002046]"
    assert tier_of(suggestion) == "envo_expansion"


def test_enforce_accepts_uberon_for_host_associated() -> None:
    """A value legal for one interface in play must not be rejected by a stricter one."""
    output = enforce_env_triad_values(
        one_suggestion("gut [UBERON:0001555]"), ["SoilInterface", "HostAssociatedInterface"]
    )
    assert output.metadata_fields[0].value == "gut [UBERON:0001555]"


def test_enforce_replaces_an_invalid_value_with_the_fallback() -> None:
    output = enforce_env_triad_values(one_suggestion("made up [ENVO:09999999]"), ["AirInterface"])
    suggestion = output.metadata_fields[0]
    assert suggestion.value == "air [ENVO:00002005]"
    assert tier_of(suggestion) == "generalized"
    assert "failed ENVO validation" in suggestion.reason


def test_enforce_leaves_non_triad_fields_alone() -> None:
    output = enforce_env_triad_values(one_suggestion("10 cm", slot="depth"), ["SoilInterface"])
    assert output.metadata_fields[0].value == "10 cm"


# --- unknown interfaces (the agentic path) ----------------------------------
#
# agentic() has no interface list to pass, so every extension is treated as
# possible. The prefix check goes lenient; nothing else does.


def test_unknown_interfaces_keep_a_prefix_some_extension_allows() -> None:
    """UBERON is legal for host-associated, so it must survive an unknown caller."""
    output = enforce_env_triad_values(one_suggestion("gut [UBERON:0001555]"), None)
    assert output.metadata_fields[0].value == "gut [UBERON:0001555]"


def test_unknown_interfaces_still_label_a_curated_value_correctly() -> None:
    """Interfaces iterate in enum order and BuiltEnv, which is first, has no value
    set -- taking its verdict would mislabel every curated value as expansion."""
    output = enforce_env_triad_values(one_suggestion("soil [ENVO:00001998]"), None)
    suggestion = output.metadata_fields[0]
    assert suggestion.value == "soil [ENVO:00001998]"
    assert isinstance(suggestion.provenance, TriadProvenance)
    assert tier_of(suggestion) == "submission_enum"
    assert suggestion.provenance.outcome == "accepted"
    assert suggestion.provenance.original_value is None
    # scoped=False records that the caller never named the extension: the gate found the
    # value curated under SoilInterface, which is not the same as knowing the sample is soil.
    assert suggestion.provenance.interface == "SoilInterface"
    assert suggestion.provenance.scoped is False


def test_unknown_interfaces_still_reject_a_dead_curie() -> None:
    output = enforce_env_triad_values(one_suggestion("subterranean lake [ENVO:02000145]"), None)
    assert provenance_of(output).tier == "generalized"


def test_unknown_interfaces_still_reject_a_biome_as_a_medium() -> None:
    output = enforce_env_triad_values(
        one_suggestion("temperate grassland biome [ENVO:01000193]"), None
    )
    assert provenance_of(output).tier == "generalized"


def test_unknown_interfaces_still_correct_label_drift() -> None:
    output = enforce_env_triad_values(one_suggestion("dirt [ENVO:00001998]"), None)
    suggestion = output.metadata_fields[0]
    assert suggestion.value == "soil [ENVO:00001998]"
    assert isinstance(suggestion.provenance, TriadProvenance)
    assert suggestion.provenance.outcome == "repaired"
    assert suggestion.provenance.original_value == "dirt [ENVO:00001998]"
    assert suggestion.provenance.scoped is False


# --- the curated-set waiver, and who it applies to ---------------------------
#
# The anchor gate is waived for a value in the extension's own curated set, so we do not
# reject what the schema offers. rhizosphere is the case that matters: ENVO does not class
# it as an environmental material, but PlantAssociatedInterface lists it as an env_medium.


def triad(local: str, medium: str, sample_id: str = "s1") -> LLMOutput:
    return LLMOutput(
        metadata_fields=[
            MetadataFieldSuggestion(
                id=sample_id, field_name="env_local_scale", value=local, reason="r"
            ),
            MetadataFieldSuggestion(
                id=sample_id, field_name="env_medium", value=medium, reason="r"
            ),
        ]
    )


def test_a_named_extension_may_waive_the_anchor_for_its_own_valueset() -> None:
    output = enforce_env_triad_values(
        one_suggestion("rhizosphere [ENVO:00005801]"), ["PlantAssociatedInterface"]
    )
    assert output.metadata_fields[0].value == "rhizosphere [ENVO:00005801]"


def test_another_extensions_valueset_does_not_waive_the_anchor() -> None:
    """Soil samples must not inherit the plant-associated list's off-anchor entry."""
    output = enforce_env_triad_values(
        one_suggestion("rhizosphere [ENVO:00005801]"), ["SoilInterface"]
    )
    assert provenance_of(output).tier == "generalized"


def test_unknown_extensions_do_not_waive_the_anchor_either() -> None:
    """The agentic path has no interface list; that is not licence to take the loosest one."""
    output = enforce_env_triad_values(one_suggestion("rhizosphere [ENVO:00005801]"), None)
    assert provenance_of(output).tier == "generalized"


# --- one term cannot answer two slots ----------------------------------------


def test_a_term_reused_across_slots_is_kept_where_its_anchor_fits() -> None:
    """Seen in a real run: rhizosphere returned as both local scale and medium."""
    output = enforce_env_triad_values(
        triad("rhizosphere [ENVO:00005801]", "rhizosphere [ENVO:00005801]"),
        ["PlantAssociatedInterface"],
    )
    local, medium = output.metadata_fields
    assert local.value == "rhizosphere [ENVO:00005801]"
    assert tier_of(medium) == "generalized"
    assert "Reused the env_local_scale term" in medium.reason


def test_a_term_fitting_both_slots_is_left_alone() -> None:
    """soil satisfies both anchors, so there is nothing to arbitrate on."""
    output = enforce_env_triad_values(
        triad("soil [ENVO:00001998]", "soil [ENVO:00001998]"), ["SoilInterface"]
    )
    assert all(f.value == "soil [ENVO:00001998]" for f in output.metadata_fields)


def test_distinct_terms_across_slots_are_untouched() -> None:
    output = enforce_env_triad_values(
        triad("rhizosphere [ENVO:00005801]", "soil [ENVO:00001998]"), ["SoilInterface"]
    )
    assert [f.value for f in output.metadata_fields] == [
        "rhizosphere [ENVO:00005801]",
        "soil [ENVO:00001998]",
    ]


def test_coherence_is_scoped_to_one_sample() -> None:
    """Two samples may legitimately share a term in the same slot."""
    output = LLMOutput(
        metadata_fields=[
            *triad("rhizosphere [ENVO:00005801]", "soil [ENVO:00001998]", "s1").metadata_fields,
            *triad("rhizosphere [ENVO:00005801]", "soil [ENVO:00001998]", "s2").metadata_fields,
        ]
    )
    assert all(
        tier_of(f) != "generalized"
        for f in enforce_env_triad_values(output, ["SoilInterface"]).metadata_fields
    )


def test_enforce_replaces_unusable_values() -> None:
    """The docstring promises no empty value reaches a caller; these used to be skipped."""
    for value in ("", "   ", ["soil [ENVO:00001998]"]):
        output = enforce_env_triad_values(one_suggestion(value), ["SoilInterface"])
        suggestion = output.metadata_fields[0]
        assert suggestion.value == "soil [ENVO:00001998]"
        assert isinstance(suggestion.provenance, TriadProvenance)
        assert suggestion.provenance.outcome == "replaced"


def test_enforce_accepts_portal_names_for_interfaces() -> None:
    """A portal name must scope the gate exactly as the interface class name does."""
    for names in (["soil"], ["SoilInterface"]):
        output = enforce_env_triad_values(one_suggestion("soil [ENVO:00001998]"), names)
        provenance = output.metadata_fields[0].provenance
        assert isinstance(provenance, TriadProvenance)
        assert provenance.tier == "submission_enum"
        assert provenance.interface == "SoilInterface"
        assert provenance.scoped is True


def test_a_model_chosen_fallback_is_labelled_generalized() -> None:
    """`generalized` used to be reachable only when the gate substituted a value."""
    output = enforce_env_triad_values(one_suggestion("air [ENVO:00002005]"), ["AirInterface"])
    provenance = output.metadata_fields[0].provenance
    assert isinstance(provenance, TriadProvenance)
    assert provenance.tier == "generalized"
    assert provenance.outcome == "accepted"
    assert provenance.interface == "AirInterface"


def test_a_curated_value_that_is_also_the_fallback_stays_tier_one() -> None:
    """soil [ENVO:00001998] is both the soil fallback and a curated value."""
    output = enforce_env_triad_values(one_suggestion("soil [ENVO:00001998]"), ["SoilInterface"])
    provenance = output.metadata_fields[0].provenance
    assert isinstance(provenance, TriadProvenance)
    assert provenance.tier == "submission_enum"


def test_provenance_survives_serialization() -> None:
    """The field is annotated as the base class, so the subclass needs SerializeAsAny.

    Every other test here reads the in-memory object, where the subclass fields are
    always present; without this the tier could be dropped on the way out and nothing
    would fail.
    """
    output = enforce_env_triad_values(one_suggestion("dirt [ENVO:00001998]"), ["SoilInterface"])
    dumped = json.loads(output.model_dump_json())["metadata_fields"][0]["provenance"]
    assert dumped["gate"] == "env_triad"
    assert dumped["tier"] == "submission_enum"
    assert dumped["outcome"] == "repaired"
    assert dumped["original_value"] == "dirt [ENVO:00001998]"
