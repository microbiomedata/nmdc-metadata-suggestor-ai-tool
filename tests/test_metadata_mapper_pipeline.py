"""The mapper pipeline keeps the agent's answer when ResultMessage.structured_output is empty.

Regression for the issue 177 eval run 37512491822: two of three mapper runs finished normally,
answered through the StructuredOutput tool, and came back empty, because the fallback that
recovers that tool call only recognized LLMOutput's ``metadata_fields``.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nmdc_metadata_suggestor_ai_tool.langfuse_claude_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
)
from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient, structured_output_from_tool_use
from nmdc_metadata_suggestor_ai_tool.metadata_mapper import pipeline
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.pipeline import (
    MAPPER_OUTPUT_KEYS,
    run_metadata_mapper_agentic,
)
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import SourceFile

MAPPER_ANSWER = {
    "source_files": [],
    "high_confidence": [
        {
            "source_column": "Sample ID",
            "source_file_id": "f1",
            "mixs_extension": "Water",
            "nmdc_candidate_slots": ["samp_name"],
            "confidence": "high",
            "reason": "sample identifier",
        }
    ],
    "needs_review": [],
    "cant_place": [
        {
            "source_column": "notes",
            "source_file_id": "f1",
            "confidence": "cant_place",
            "reason": "free text",
        }
    ],
}


def tool_call_event(payload: dict[str, Any]) -> AssistantMessage:
    from claude_agent_sdk import ToolUseBlock

    block = ToolUseBlock(id="t1", name="StructuredOutput", input={"output": json.dumps(payload)})
    return AssistantMessage(content=[block], model="claude-test")


def result_event(structured_output: Any) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1000,
        duration_api_ms=900,
        is_error=False,
        num_turns=3,
        session_id="s1",
        total_cost_usd=0.5,
        structured_output=structured_output,
    )


class TestFallbackKeys:
    def test_default_still_recovers_llm_output(self) -> None:
        event = tool_call_event({"metadata_fields": [{"id": "x"}]})
        assert structured_output_from_tool_use(event) is not None

    def test_default_drops_mapper_answer(self) -> None:
        # The behavior that lost the eval's answers; kept as the default for LLMOutput callers.
        assert structured_output_from_tool_use(tool_call_event(MAPPER_ANSWER)) is None

    def test_mapper_keys_recover_mapper_answer(self) -> None:
        recovered = structured_output_from_tool_use(
            tool_call_event(MAPPER_ANSWER), MAPPER_OUTPUT_KEYS
        )
        assert recovered == MAPPER_ANSWER

    def test_empty_wrapper_is_not_an_answer(self) -> None:
        empty: dict[str, list[Any]] = {"high_confidence": [], "needs_review": [], "cant_place": []}
        assert structured_output_from_tool_use(tool_call_event(empty), MAPPER_OUTPUT_KEYS) is None


def run_with_fake_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, events: list[Any]) -> Any:
    async def fake_query(prompt: str, options: Any) -> AsyncIterator[Any]:
        for event in events:
            yield event

    monkeypatch.setattr(pipeline, "query", fake_query)
    csv_path = tmp_path / "samples.csv"
    csv_path.write_text("Sample ID,notes\nS1,dry\n")
    client = LLMClient.__new__(LLMClient)
    client.access_provider = "cborg"
    client.model = "claude-test"
    output, _ = asyncio.run(
        run_metadata_mapper_agentic(
            llm_client=client,
            csv_files=[(SourceFile(file_id="f1", display_name="samples.csv"), csv_path)],
            mixs_extensions=["water"],
        )
    )
    return output


def test_answer_survives_empty_result_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events = [
        SystemMessage(subtype="init", data={"session_id": "s1"}),
        tool_call_event(MAPPER_ANSWER),
        result_event(structured_output=None),
    ]
    output = run_with_fake_agent(monkeypatch, tmp_path, events)
    assert [m.source_column for m in output.high_confidence] == ["Sample ID"]
    assert [m.source_column for m in output.cant_place] == ["notes"]
    assert output.run_health["num_turns"] == 3


def test_empty_run_is_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    events = [
        SystemMessage(subtype="init", data={"session_id": "s1"}),
        result_event(structured_output=None),
    ]
    with caplog.at_level(logging.WARNING, logger=pipeline.logger.name):
        output = run_with_fake_agent(monkeypatch, tmp_path, events)
    assert not (output.high_confidence or output.needs_review or output.cant_place)
    messages = " | ".join(r.getMessage() for r in caplog.records)
    assert "no structured output" in messages
    assert "returned no mappings (turns=3, cost=0.5" in messages
