"""Weekly integration test: run the full pipeline against VertexAI and validate output.

This test is excluded from normal CI runs (marker = integration).
It is executed by the weekly-integration-test GitHub Actions workflow
to confirm that the VertexAI endpoint is reachable and produces valid output.
"""

import json
from pathlib import Path

import pytest

from nmdc_metadata_suggestor_ai_tool.llm_client import ConversationManager, LLMClient
from nmdc_metadata_suggestor_ai_tool.recommendation_pipeline import run_recommendation_pipeline
from nmdc_metadata_suggestor_ai_tool.utils.utils import validate_output

FIXTURES_DIR = Path(__file__).parent / "fixtures"

INTEGRATION_TIMEOUT = 120  # seconds


def _load_fixture(filename: str) -> dict:
    """Load a JSON fixture by filename."""
    with (FIXTURES_DIR / filename).open() as f:
        return json.load(f)


@pytest.mark.integration
@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_pipeline_returns_valid_llm_output() -> None:
    """Run the full recommendation pipeline and validate the LLMOutput model."""
    submission = _load_fixture("test_submission.json")
    client = LLMClient(access_provider="gcp")

    result = run_recommendation_pipeline(
        submission_object=submission,
        llm_client=client,
    )

    assert result.metadata_fields, "Expected at least one metadata field suggestion"

    fields_with_values = 0
    for field in result.metadata_fields:
        assert field.field_name, "field_name must not be empty"
        assert field.reason, "reason must not be empty"
        if field.value != "":
            fields_with_values += 1

    assert fields_with_values > 0, "Expected at least one field with a suggested value"

    assert result.model, "model should be a non-empty string"
    assert result.access_provider == "gcp"


@pytest.mark.integration
@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_gemini_conversation_with_schema_context() -> None:
    """Verify the Gemini model can process schema context and return valid JSON."""
    client = LLMClient(access_provider="gcp")
    conversation = ConversationManager(llm_client=client)
    conversation.add_schema_context('{"fields": [{"name": "env_broad_scale", "type": "string"}]}')
    conversation.add_message(
        text=(
            "Given a soil metagenome study, suggest a value for env_broad_scale. "
            "Reply with JSON matching this schema: "
            '{"metadata_fields": [{"field_name": "env_broad_scale",'
            ' "reason": "<why>", "value": "<your suggestion>"}]}'
        )
    )
    response = conversation.generate()

    assert response, "Expected a non-empty response from Gemini"
    result = validate_output(response)
    assert result.metadata_fields, "Expected at least one metadata field suggestion"
    assert any(f.field_name == "env_broad_scale" for f in result.metadata_fields), (
        "Response must contain 'env_broad_scale'"
    )
    assert any(f.value for f in result.metadata_fields), (
        "At least one field must have a suggested value"
    )
