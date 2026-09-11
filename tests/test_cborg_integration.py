"""Integration tests for the CBORG access provider.

CBORG is LBL's OpenAI- and Anthropic-compatible LiteLLM gateway. These tests
hit the real endpoint, so they are marked ``integration`` and excluded from
normal CI (see ``addopts = "-m 'not integration'"`` in pyproject.toml). They
skip when CBORG credentials are absent.

Run them with:
    uv run pytest -m integration tests/test_cborg_integration.py

Credentials (``CBORG_KEY``, ``CBORG_BASE_URL``) are read from the environment,
which ``llm_client`` populates from ``.env`` via ``load_dotenv()`` at import.

The model defaults to the same model as the production CBORG path. Override it
with ``CBORG_TEST_MODEL`` to exercise another CBORG-served model explicitly.
"""

import json
import os
from pathlib import Path

import pytest
from openai import OpenAI

from nmdc_metadata_suggestor_ai_tool.llm_client import DEFAULT_GEMINI_MODEL, LLMClient
from nmdc_metadata_suggestor_ai_tool.recommendation_pipeline import run_recommendation_pipeline

FIXTURES_DIR = Path(__file__).parent / "fixtures"
INTEGRATION_TIMEOUT = 120  # seconds
INTEGRATION_MAX_TOKENS = 8192
CBORG_MODEL = os.environ.get("CBORG_TEST_MODEL", DEFAULT_GEMINI_MODEL)


def _load_fixture(filename: str) -> dict:
    with (FIXTURES_DIR / filename).open() as f:
        return json.load(f)


@pytest.mark.integration
@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_cborg_responses_api_reachable(requires_cborg: None) -> None:
    """The CBORG Responses API accepts an authenticated request with the .env key.

    This is the low-level check: credentials, base URL, and configured model
    work through the same endpoint used by the recommendation pipeline.
    """
    client = OpenAI(
        api_key=os.environ["CBORG_KEY"],
        base_url=os.environ["CBORG_BASE_URL"],
    )
    response = client.responses.create(
        model=CBORG_MODEL,
        input="Reply with a short acknowledgement.",
        max_output_tokens=16,
    )
    assert response.output_text.strip(), "CBORG returned no response text"


@pytest.mark.integration
@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_cborg_pipeline_returns_field_guidance(requires_cborg: None) -> None:
    """The recommendation pipeline runs end to end over CBORG and returns valid output.

    This is the field-guidance task: each suggestion names a metadata field the
    submitter should populate, with a reason. Suggested values are optional for
    this task, so the test does not require them.
    """
    submission = _load_fixture("full_submission.json")
    client = LLMClient(access_provider="cborg", model=CBORG_MODEL)

    result = run_recommendation_pipeline(
        submission_object=submission,
        llm_client=client,
        max_tokens=INTEGRATION_MAX_TOKENS,
    )

    assert result.metadata_fields, "Expected at least one metadata field suggestion"
    for field in result.metadata_fields:
        assert field.field_name, "field_name must not be empty"
        assert field.reason, "reason must not be empty"

    assert result.model == CBORG_MODEL
    assert result.access_provider == "cborg"
