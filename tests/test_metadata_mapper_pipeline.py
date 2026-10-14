"""The mapper pipeline keeps the agent's answer when ResultMessage.structured_output is empty.

Regression for the issue 177 eval run 37512491822: two of three mapper runs finished normally,
answered through the StructuredOutput tool, and came back empty, because the fallback that
recovers that tool call only recognized LLMOutput's ``metadata_fields``.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from nmdc_metadata_suggestor_ai_tool.langfuse_claude_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
)
from nmdc_metadata_suggestor_ai_tool.llm_client import (
    LLMClient,
    answer_fields,
    structured_output_from_tool_use,
)
from nmdc_metadata_suggestor_ai_tool.metadata_mapper import pipeline
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.pipeline import run_metadata_mapper_agentic
from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    MetadataMapperOutput,
    SourceFile,
)

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


class TestFallbackOutputModel:
    def test_answer_fields_come_from_the_model(self) -> None:
        assert answer_fields(LLMOutput) == ("metadata_fields",)
        assert set(answer_fields(MetadataMapperOutput)) == {
            "source_files",
            "high_confidence",
            "needs_review",
            "cant_place",
        }

    def test_shared_scalar_fields_do_not_make_an_answer(self) -> None:
        # Both models have ``model``; a payload with only that is not an answer to either.
        only_model = {"model": "claude-test", "high_confidence": [], "metadata_fields": []}
        event = tool_call_event(only_model)
        assert structured_output_from_tool_use(event, MetadataMapperOutput) is None
        assert structured_output_from_tool_use(event, LLMOutput) is None

    def test_default_still_recovers_llm_output(self) -> None:
        event = tool_call_event({"metadata_fields": [{"id": "x"}]})
        assert structured_output_from_tool_use(event) is not None

    def test_llm_output_callers_ignore_mapper_payloads(self) -> None:
        # A recommendation pipeline run must not pick up a Metadata Mapper answer as its own.
        event = tool_call_event(MAPPER_ANSWER)
        assert structured_output_from_tool_use(event, LLMOutput) is None

    def test_mapper_model_recovers_mapper_answer(self) -> None:
        recovered = structured_output_from_tool_use(
            tool_call_event(MAPPER_ANSWER), MetadataMapperOutput
        )
        assert recovered == MAPPER_ANSWER

    def test_empty_wrapper_is_not_an_answer(self) -> None:
        empty: dict[str, list[Any]] = {"high_confidence": [], "needs_review": [], "cant_place": []}
        assert structured_output_from_tool_use(tool_call_event(empty), MetadataMapperOutput) is None


@pytest.fixture
def span_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stand-in Langfuse client that records every span update."""
    client = MagicMock()
    monkeypatch.setattr(pipeline, "langfuse_client", client)
    return client


def final_span_update(span_client: MagicMock) -> dict[str, Any]:
    """Keyword arguments of the last update_current_span call: the end-of-run one."""
    return dict(span_client.update_current_span.call_args_list[-1].kwargs)


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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, span_client: MagicMock
) -> None:
    events = [
        SystemMessage(subtype="init", data={"session_id": "s1"}),
        tool_call_event(MAPPER_ANSWER),
        result_event(structured_output=None),
    ]
    output = run_with_fake_agent(monkeypatch, tmp_path, events)
    assert [m.source_column for m in output.high_confidence] == ["Sample ID"]
    assert [m.source_column for m in output.cant_place] == ["notes"]

    span = final_span_update(span_client)
    assert span["level"] == "DEFAULT"
    assert span["status_message"] is None
    assert span["metadata"]["output_source"] == "tool_use_fallback"


def test_empty_run_is_reported_on_span(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, span_client: MagicMock
) -> None:
    events = [
        SystemMessage(subtype="init", data={"session_id": "s1"}),
        result_event(structured_output=None),
    ]
    output = run_with_fake_agent(monkeypatch, tmp_path, events)
    assert not (output.high_confidence or output.needs_review or output.cant_place)

    span = final_span_update(span_client)
    assert span["level"] == "WARNING"
    assert span["status_message"] == "Metadata mapper returned no mappings"
    assert span["metadata"]["output_source"] == "none"
    assert span["metadata"]["num_turns"] == 3
    # The span records the empty output, not None.
    assert span["output"] == output


def test_run_without_result_message_records_empty_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, span_client: MagicMock
) -> None:
    # No ResultMessage at all: before, the span was updated before the result was settled
    # and recorded output=None.
    events = [SystemMessage(subtype="init", data={"session_id": "s1"})]
    output = run_with_fake_agent(monkeypatch, tmp_path, events)
    span = final_span_update(span_client)
    assert span["output"] == output
    assert span["output"] is not None
    assert span["level"] == "WARNING"
