"""Integration tests for ConversationManager.agentic() using real fixture data.

Marked `integration` — excluded from normal CI. Requires GCP credentials.
Run with: uv run pytest -m integration tests/test_agentic.py
"""

import asyncio
import json
from pathlib import Path

import pytest

from nmdc_metadata_suggestor_ai_tool.llm_client import ConversationManager, LLMClient
from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput
from nmdc_metadata_suggestor_ai_tool.system_prompt import env_triad_prompt, system_prompt

FIXTURES = Path(__file__).parent / "fixtures"
INTEGRATION_TIMEOUT = 180  # seconds


def _load(filename: str) -> dict:
    with (FIXTURES / filename).open() as f:
        return json.load(f)


def _make_conversation_manager(prompt: str) -> ConversationManager:
    client = LLMClient(access_provider="gcp")
    return ConversationManager(llm_client=client, system_prompt=prompt)


def _assert_valid_output(result: object) -> LLMOutput:
    assert result is not None, "agentic() returned None"
    structured, session_id = result
    assert session_id is not None, "Expected a session_id to be returned"
    output = (
        structured if isinstance(structured, LLMOutput) else LLMOutput.model_validate(structured)
    )
    assert isinstance(output, LLMOutput), f"Expected LLMOutput, got {type(output)}"
    assert output.metadata_fields, "Expected at least one metadata field suggestion"
    for field in output.metadata_fields:
        assert field.field_name, "field_name must not be empty"
        assert field.reason, "reason must not be empty"
    return output


# ---------------------------------------------------------------------------
# Metadata suggestion pipeline (full submission object)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_agentic_metadata_pipeline_with_full_submission(requires_credentials: None) -> None:
    """Full submission object → metadata field suggestions via agentic()."""
    submission = _load("full_submission_chopped.json")
    cm = _make_conversation_manager(system_prompt)

    result = asyncio.run(cm.agentic(message=str(submission)))
    output = _assert_valid_output(result)

    fields_with_values = sum(1 for f in output.metadata_fields if f.value != "")
    assert fields_with_values > 0, "Expected at least one field with a non-empty value"


@pytest.mark.integration
@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_agentic_metadata_pipeline_with_test_submission(requires_credentials: None) -> None:
    """test_submission.json fixture → metadata field suggestions via agentic()."""
    submission = _load("test_submission.json")
    cm = _make_conversation_manager(system_prompt)

    result = asyncio.run(cm.agentic(message=str(submission)))
    _assert_valid_output(result)


# ---------------------------------------------------------------------------
# Env triad (ETL-shaped biosample + study context)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_agentic_env_triad_single_biosample(requires_credentials: None) -> None:
    """Single ETL-shaped biosample → env triad suggestions via agentic()."""
    biosample = _load("biosample.json")
    samples = biosample.get("biosample_set", [biosample])[:1]

    message = {
        "samples": samples,
        "interface_names": ["soil"],
    }
    cm = _make_conversation_manager(env_triad_prompt)

    result = asyncio.run(cm.agentic(message=str(message)))
    output = _assert_valid_output(result)

    field_names = {f.field_name for f in output.metadata_fields}
    env_triad_fields = {"env_broad_scale", "env_local_scale", "env_medium"}
    assert field_names & env_triad_fields, (
        f"Expected at least one env triad field in output, got: {field_names}"
    )


@pytest.mark.integration
@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_agentic_env_triad_with_study_context(requires_credentials: None) -> None:
    """ETL biosamples + study context → env triad suggestions via agentic()."""
    biosamples = _load("bioscales_biosamples.json")
    study = _load("bioscales_study.json")
    samples = biosamples.get("resources", [])[:5]

    message = {
        "samples": samples,
        "study_context": [study],
        "interface_names": ["soil", "plant-associated"],
    }
    cm = _make_conversation_manager(env_triad_prompt)

    result = asyncio.run(cm.agentic(message=str(message)))
    output = _assert_valid_output(result)

    # each sample should have at least one env triad field
    returned_ids = {f.id for f in output.metadata_fields if f.id}
    input_ids = {str(s.get("id", str(i))) for i, s in enumerate(samples)}
    assert returned_ids & input_ids, "Expected output fields to reference input sample IDs"


# ---------------------------------------------------------------------------
# Session resumption
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.timeout(INTEGRATION_TIMEOUT * 2)
def test_agentic_session_resumption(requires_credentials: None) -> None:
    """Start a session, then resume it with a follow-up message."""
    submission = _load("test_submission.json")
    cm = _make_conversation_manager(system_prompt)

    # first turn — start session
    result1 = asyncio.run(cm.agentic(message=str(submission)))
    assert result1 is not None
    _, session_id = result1
    assert session_id, "Expected a session_id after first turn"

    # second turn — resume session
    result2 = asyncio.run(
        cm.agentic(
            session_id=session_id,
            message="Focus only on fields related to the study's geographic location.",
        )
    )
    output = _assert_valid_output(result2)
    assert output.metadata_fields, "Expected suggestions in resumed session"
