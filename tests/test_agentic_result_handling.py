"""Tests for how agentic() recovers and reports its result.

No network: these drive the message-handling directly with SDK-shaped stubs. The behaviour
they pin was found in a real run where the agent passed 27 suggestions to the StructuredOutput
tool, ResultMessage carried none of them, and the call reported success with zero fields.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from nmdc_metadata_suggestor_ai_tool.llm_client import ConversationManager


@dataclass
class FakeToolUse:
    name: str
    input: dict[str, Any]


@dataclass
class FakeAssistantMessage:
    content: list[Any] = field(default_factory=list)


@dataclass
class FakeResultMessage:
    structured_output: Any = None
    permission_denials: list[Any] | None = None
    errors: list[str] | None = None
    is_error: bool = False
    num_turns: int = 1
    stop_reason: str | None = None
    terminal_reason: str | None = None


def suggestions(value: str = "soil [ENVO:00001998]") -> dict[str, Any]:
    return {
        "metadata_fields": [{"id": "s1", "field_name": "env_medium", "value": value, "reason": "r"}]
    }


# --- recovering the payload -------------------------------------------------


def test_tool_payload_is_read_from_a_structured_output_call() -> None:
    event = FakeAssistantMessage([FakeToolUse("StructuredOutput", {"output": suggestions()})])
    assert ConversationManager.structured_output_from_tool_use(event) == suggestions()


def test_tool_payload_parses_a_json_string_argument() -> None:
    event = FakeAssistantMessage(
        [FakeToolUse("StructuredOutput", {"output": json.dumps(suggestions())})]
    )
    assert ConversationManager.structured_output_from_tool_use(event) == suggestions()


def test_other_tool_calls_are_ignored() -> None:
    event = FakeAssistantMessage([FakeToolUse("Bash", {"command": "ls"})])
    assert ConversationManager.structured_output_from_tool_use(event) is None


def test_result_message_wins_when_it_carries_fields() -> None:
    cm = ConversationManager.__new__(ConversationManager)
    out = cm.finalize_result(FakeResultMessage(structured_output=suggestions()), suggestions())
    assert len(out.metadata_fields) == 1


def test_empty_result_message_falls_back_to_the_tool_call() -> None:
    """The real failure: 27 fields sent to the tool, none on the ResultMessage."""
    cm = ConversationManager.__new__(ConversationManager)
    out = cm.finalize_result(
        FakeResultMessage(structured_output={"metadata_fields": []}), suggestions()
    )
    assert [f.value for f in out.metadata_fields] == ["soil [ENVO:00001998]"]


def test_recovered_output_still_passes_the_env_triad_gate() -> None:
    """Recovery must not become a way around validation."""
    cm = ConversationManager.__new__(ConversationManager)
    out = cm.finalize_result(
        FakeResultMessage(structured_output=None),
        suggestions("temperate coniferous forest biome [ENVO:01000219]"),
    )
    assert out.metadata_fields[0].value != "temperate coniferous forest biome [ENVO:01000219]"


def test_nothing_anywhere_is_an_error_not_an_empty_result() -> None:
    cm = ConversationManager.__new__(ConversationManager)
    with pytest.raises(ValueError, match="No usable structured output"):
        cm.finalize_result(FakeResultMessage(structured_output=None), None)


# --- reporting how the run went ---------------------------------------------


def test_permission_denials_are_counted_and_grouped() -> None:
    event = FakeResultMessage(
        permission_denials=[{"tool_name": "Bash"}, {"tool_name": "Bash"}, {"tool_name": "Write"}]
    )
    health = ConversationManager.run_health(event)
    assert health["permission_denials"] == 3
    assert health["permission_denials_by_tool"] == {"Bash": 2, "Write": 1}


def test_errors_and_terminal_reason_are_surfaced() -> None:
    event = FakeResultMessage(is_error=True, errors=["boom"], terminal_reason="max_turns")
    health = ConversationManager.run_health(event)
    assert health["errors"] == ["boom"]
    assert health["terminal_reason"] == "max_turns"


def test_a_clean_run_reports_no_denials() -> None:
    assert ConversationManager.run_health(FakeResultMessage())["permission_denials"] == 0


# --- the tool argument name is not stable -----------------------------------


@pytest.mark.parametrize("key", ["output", "json_output", "some_future_name"])
def test_payload_is_found_whatever_the_argument_is_called(key: str) -> None:
    """Seen as both `output` and `json_output` across runs on the same SDK."""
    event = FakeAssistantMessage([FakeToolUse("StructuredOutput", {key: suggestions()})])
    assert ConversationManager.structured_output_from_tool_use(event) == suggestions()


@pytest.mark.parametrize("key", ["output", "json_output"])
def test_payload_is_found_when_it_is_a_json_string(key: str) -> None:
    event = FakeAssistantMessage(
        [FakeToolUse("StructuredOutput", {key: json.dumps(suggestions())})]
    )
    assert ConversationManager.structured_output_from_tool_use(event) == suggestions()


def test_an_empty_wrapper_is_not_mistaken_for_a_result() -> None:
    """Requiring metadata_fields stops a shell dict passing as a recovered answer."""
    event = FakeAssistantMessage([FakeToolUse("StructuredOutput", {"json_output": {"other": 1}})])
    assert ConversationManager.structured_output_from_tool_use(event) is None
