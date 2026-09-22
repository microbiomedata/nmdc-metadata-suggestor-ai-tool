"""Schema validation hooks for metadata mapper output."""

import json
from typing import Any

from nmdc_metadata_suggestor_ai_tool.langfuse_claude_sdk import (
    AsyncHookJSONOutput,
    HookContext,
    HookInput,
    PostToolUseHookInput,
    SyncHookJSONOutput,
)
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    ColumnMapping,
    MetadataMapperOutput,
)
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder

STRUCTURED_OUTPUT_TOOL = "StructuredOutput"


def validate_mapper_output(
    output: MetadataMapperOutput,
    schema_builder: SchemaContextBuilder | None = None,
) -> MetadataMapperOutput:
    """Validate mapped slots against the interface reported by the agent.

    Invalid mappings are demoted to ``cant_place`` so they cannot be applied to
    CSV rows with an unverified NMDC slot key.
    """
    builder = schema_builder or SchemaContextBuilder()
    interfaces = {name.casefold(): name for name in builder.list_interfaces()}
    valid_mappings: list[ColumnMapping] = []
    invalid_mappings: list[ColumnMapping] = []

    for mapping in output.high_confidence + output.needs_review:
        error = _mapping_error(mapping, builder, interfaces)
        if error is None:
            valid_mappings.append(mapping)
            continue
        _demote_mapping(mapping, error)
        invalid_mappings.append(mapping)

    output.high_confidence = [
        mapping for mapping in valid_mappings if mapping.confidence == "high"
    ]
    output.needs_review = [
        mapping for mapping in valid_mappings if mapping.confidence == "review"
    ]
    output.cant_place.extend(invalid_mappings)

    # A cant_place mapping should never carry candidate slots, even if the
    # agent populated them despite the output contract.
    for mapping in output.cant_place:
        if mapping.confidence != "cant_place" or mapping.nmdc_candidate_slots:
            _demote_mapping(mapping, "mapping is marked cant_place")

    return output


def metadata_mapper_validation_hook(
    input_data: HookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> AsyncHookJSONOutput | SyncHookJSONOutput:
    """Return schema errors to the mapper agent after StructuredOutput calls.

    Post-tool hooks cannot reject a completed tool call, so invalid output is
    returned as additional context. The agent can then correct its JSON and call
    StructuredOutput again.
    """
    if input_data["hook_event_name"] != "PostToolUse":
        return {}
    posttool: PostToolUseHookInput = input_data  # type: ignore[assignment]
    if posttool["tool_name"] != STRUCTURED_OUTPUT_TOOL:
        return {}

    output = _parse_mapper_output(posttool.get("tool_response"))
    if output is None:
        return {}

    errors = mapper_output_schema_errors(output)
    if not errors:
        return {}

    correction = "\n".join(f"- {error}" for error in errors)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                "The MetadataMapperOutput failed NMDC schema validation. "
                "Correct the mappings and call StructuredOutput again.\n"
                f"{correction}"
            ),
        }
    }


def mapper_output_schema_errors(
    output: MetadataMapperOutput,
    schema_builder: SchemaContextBuilder | None = None,
) -> list[str]:
    """Return actionable schema errors without mutating the mapper output."""
    builder = schema_builder or SchemaContextBuilder()
    interfaces = {name.casefold(): name for name in builder.list_interfaces()}
    errors: list[str] = []
    for mapping in output.high_confidence + output.needs_review:
        error = _mapping_error(mapping, builder, interfaces)
        if error is not None:
            errors.append(f"{mapping.source_column}: {error}")
    return errors


def _parse_mapper_output(raw: Any) -> MetadataMapperOutput | None:
    if isinstance(raw, MetadataMapperOutput):
        return raw
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    candidates = (raw, *raw.values())
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            return MetadataMapperOutput.model_validate(candidate)
        except Exception:
            continue
    return None


def _mapping_error(
    mapping: ColumnMapping,
    builder: SchemaContextBuilder,
    interfaces: dict[str, str],
) -> str | None:
    if not mapping.nmdc_candidate_slots:
        return None

    extension = mapping.mixs_extension or ""
    interface_key = (
        extension if extension.casefold().endswith("interface") else f"{extension}Interface"
    )
    interface_name = interfaces.get(interface_key.casefold())
    if interface_name is None:
        return f"unknown NMDC interface for MIxS extension {extension!r}"

    schema = builder.get_interface_schema(interface_name)
    interface_slots = {slot.name for slot in schema.slots}
    invalid_slots = [
        slot for slot in mapping.nmdc_candidate_slots if slot not in interface_slots
    ]
    if invalid_slots:
        return f"candidate slot(s) {invalid_slots!r} are not in {interface_name}"
    return None


def _demote_mapping(mapping: ColumnMapping, reason: str) -> None:
    mapping.confidence = "cant_place"
    mapping.nmdc_candidate_slots = []
    mapping.reason = f"Schema validation failed: {reason}. {mapping.reason}".strip()
