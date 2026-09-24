"""Tests for schema validation of metadata mapper output."""

from typing import Any, cast

import pytest

from nmdc_metadata_suggestor_ai_tool.langfuse_claude_sdk import (
    HookContext,
    PostToolUseHookInput,
)
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.pipeline import _finalize_mapper_result
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.validation import (
    metadata_mapper_validation_hook,
    validate_mapper_output,
)
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    ColumnMapping,
    MetadataMapperOutput,
)
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder


def _mapping(extension: str, slot: str) -> ColumnMapping:
    return ColumnMapping(
        source_column="value",
        source_file_id="file-1",
        mixs_extension=extension,
        nmdc_candidate_slots=[slot],
        confidence="high",
        reason="model suggestion",
    )


def test_validate_mapper_output_demotes_when_all_slots_invalid() -> None:
    output = MetadataMapperOutput(high_confidence=[_mapping("Soil", "not_a_schema_slot")])

    validated = validate_mapper_output(output)

    assert not validated.high_confidence
    assert len(validated.cant_place) == 1
    assert validated.cant_place[0].nmdc_candidate_slots == []
    assert "not in SoilInterface" in validated.cant_place[0].reason


def test_validate_mapper_output_demotes_slot_from_wrong_interface() -> None:
    builder = SchemaContextBuilder()
    soil_slots = {slot.name for slot in builder.get_interface_schema("SoilInterface").slots}
    water_slots = {slot.name for slot in builder.get_interface_schema("WaterInterface").slots}
    soil_only_slot = next(iter(soil_slots - water_slots))
    output = MetadataMapperOutput(high_confidence=[_mapping("Water", soil_only_slot)])

    validated = validate_mapper_output(output, builder)

    assert not validated.high_confidence
    assert validated.cant_place[0].nmdc_candidate_slots == []
    assert "not in WaterInterface" in validated.cant_place[0].reason


def test_validate_mapper_output_requires_enum_map_for_enum_slot() -> None:
    # biotic_relationship is enum-constrained in SoilInterface
    output = MetadataMapperOutput(high_confidence=[_mapping("Soil", "biotic_relationship")])
    validated = validate_mapper_output(output)

    assert not validated.high_confidence
    assert validated.cant_place[0].nmdc_candidate_slots == []
    assert "enum_map" in validated.cant_place[0].reason


def test_validate_mapper_output_accepts_enum_slot_with_enum_map_conversion() -> None:
    from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import ValueConversion

    mapping = ColumnMapping(
        source_column="value",
        source_file_id="file-1",
        mixs_extension="Soil",
        nmdc_candidate_slots=["biotic_relationship"],
        confidence="high",
        reason="model suggestion",
        conversion=ValueConversion(
            type="enum_map",
            description="map to permissible values",
            expression='{"free living": "free living"}',
        ),
    )
    output = MetadataMapperOutput(high_confidence=[mapping])
    validated = validate_mapper_output(output)

    assert len(validated.high_confidence) == 1
    assert not validated.cant_place


def test_validate_mapper_output_drops_invalid_secondary_slots() -> None:
    builder = SchemaContextBuilder()
    valid_slot = builder.get_interface_schema("SoilInterface").slots[0].name
    mapping = ColumnMapping(
        source_column="value",
        source_file_id="file-1",
        mixs_extension="Soil",
        nmdc_candidate_slots=[valid_slot, "not_a_schema_slot"],
        confidence="high",
        reason="model suggestion",
    )
    output = MetadataMapperOutput(high_confidence=[mapping])

    validated = validate_mapper_output(output, builder)

    assert len(validated.high_confidence) == 1
    assert validated.high_confidence[0].nmdc_candidate_slots == [valid_slot]
    assert not validated.cant_place


def test_mapper_finalizer_runs_schema_validation() -> None:
    raw = {
        "high_confidence": [
            {
                "source_column": "value",
                "source_file_id": "file-1",
                "mixs_extension": "Soil",
                "nmdc_candidate_slots": ["not_a_schema_slot"],
                "confidence": "high",
                "reason": "model suggestion",
            }
        ]
    }

    result = _finalize_mapper_result(raw)

    assert not result.high_confidence
    assert len(result.cant_place) == 1


@pytest.mark.asyncio
async def test_metadata_mapper_hook_returns_schema_errors_to_agent() -> None:
    hook_input = cast(
        PostToolUseHookInput,
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "StructuredOutput",
            "tool_input": {},
            "tool_response": {
                "high_confidence": [
                    {
                        "source_column": "value",
                        "source_file_id": "file-1",
                        "mixs_extension": "Soil",
                        "nmdc_candidate_slots": ["not_a_schema_slot"],
                        "confidence": "high",
                        "reason": "model suggestion",
                    }
                ]
            },
        },
    )

    result = await metadata_mapper_validation_hook(
        hook_input,
        None,
        cast(HookContext, {"signal": None}),
    )
    hook_output = cast(dict[str, Any], result["hookSpecificOutput"])

    assert "hookSpecificOutput" in result
    assert "not in SoilInterface" in hook_output["additionalContext"]
    assert "call StructuredOutput again" in hook_output["additionalContext"]


@pytest.mark.asyncio
async def test_metadata_mapper_hook_accepts_valid_output() -> None:
    builder = SchemaContextBuilder()
    slot = builder.get_interface_schema("SoilInterface").slots[0].name
    hook_input = cast(
        PostToolUseHookInput,
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "StructuredOutput",
            "tool_input": {},
            "tool_response": {
                "high_confidence": [
                    {
                        "source_column": "value",
                        "source_file_id": "file-1",
                        "mixs_extension": "Soil",
                        "nmdc_candidate_slots": [slot],
                        "confidence": "high",
                        "reason": "model suggestion",
                    }
                ]
            },
        },
    )

    assert (
        await metadata_mapper_validation_hook(
            hook_input,
            None,
            cast(HookContext, {"signal": None}),
        )
        == {}
    )
