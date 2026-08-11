"""Tests for the env triad pipeline's ENVO context and value enforcement.

These cover the non-agentic path in env_triad_recommendation: the ENVO candidate
context handed to the model, and the gate that repairs or replaces what comes
back. Unit tests for the index itself live in test_envo_index.py.
"""

from nmdc_metadata_suggestor_ai_tool.env_triad_recommendation import (
    build_envo_expansion_context,
    enforce_env_triad_values,
)
from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput, MetadataFieldSuggestion


def one_suggestion(value: str, slot: str = "env_medium", source: str | None = None) -> LLMOutput:
    return LLMOutput(
        metadata_fields=[
            MetadataFieldSuggestion(field_name=slot, reason="r", value=value, source=source)
        ]
    )


# --- ENVO context -----------------------------------------------------------


def test_expansion_context_always_carries_the_biome_list() -> None:
    context = build_envo_expansion_context(None)
    assert "every ENVO biome" in context
    assert "marine biome [ENVO:00000447]" in context


def test_expansion_context_names_the_extension_subtrees() -> None:
    context = build_envo_expansion_context(["WastewaterSludgeInterface"])
    assert "sludge [ENVO:00002044]" in context
    assert "Generic fallback" in context


# --- enforcement ------------------------------------------------------------


def test_enforce_repairs_label_drift() -> None:
    output = enforce_env_triad_values(one_suggestion("dirt [ENVO:00001998]"), ["SoilInterface"])
    suggestion = output.metadata_fields[0]
    assert suggestion.value == "soil [ENVO:00001998]"
    assert suggestion.source == "submission_enum"


def test_enforce_overrides_a_mislabelled_source() -> None:
    """The tier label comes from validation, not from the model's self-report."""
    output = enforce_env_triad_values(
        one_suggestion("activated sludge [ENVO:00002046]", source="submission_enum"),
        ["WastewaterSludgeInterface"],
    )
    suggestion = output.metadata_fields[0]
    assert suggestion.value == "activated sludge [ENVO:00002046]"
    assert suggestion.source == "envo_expansion"


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
    assert suggestion.source == "generalized"
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
    assert suggestion.source == "submission_enum"


def test_unknown_interfaces_still_reject_a_dead_curie() -> None:
    output = enforce_env_triad_values(one_suggestion("subterranean lake [ENVO:02000145]"), None)
    assert output.metadata_fields[0].source == "generalized"


def test_unknown_interfaces_still_reject_a_biome_as_a_medium() -> None:
    output = enforce_env_triad_values(
        one_suggestion("temperate grassland biome [ENVO:01000193]"), None
    )
    assert output.metadata_fields[0].source == "generalized"


def test_unknown_interfaces_still_correct_label_drift() -> None:
    output = enforce_env_triad_values(one_suggestion("dirt [ENVO:00001998]"), None)
    suggestion = output.metadata_fields[0]
    assert suggestion.value == "soil [ENVO:00001998]"
    assert suggestion.source == "submission_enum"
