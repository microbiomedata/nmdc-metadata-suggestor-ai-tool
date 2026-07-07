"""Integration tests for the CBORG access provider.

CBORG is LBL's OpenAI- and Anthropic-compatible LiteLLM gateway. These tests
hit the real endpoint, so they are marked ``integration`` and excluded from
normal CI (see ``addopts = "-m 'not integration'"`` in pyproject.toml). They
skip when CBORG credentials are absent.

Run them with:
    uv run pytest -m integration tests/test_cborg_integration.py

Credentials (``CBORG_KEY``, ``CBORG_BASE_URL``) are read from the environment,
which ``llm_client`` populates from ``.env`` via ``load_dotenv()`` at import.

The model is set explicitly. The built-in default model is Gemini-specific
(``gemini-2.5-flash``), which CBORG does not recognize, so provider tests must
name a model CBORG serves. Override with the ``CBORG_TEST_MODEL`` env var.
"""

import json
import os
from pathlib import Path

import pytest
from openai import OpenAI

from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient
from nmdc_metadata_suggestor_ai_tool.recommendation_pipeline import run_recommendation_pipeline

FIXTURES_DIR = Path(__file__).parent / "fixtures"
INTEGRATION_TIMEOUT = 120  # seconds
CBORG_MODEL = os.environ.get("CBORG_TEST_MODEL", "gpt-4o-mini")


def _load_fixture(filename: str) -> dict:
    with (FIXTURES_DIR / filename).open() as f:
        return json.load(f)


@pytest.mark.integration
@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_cborg_api_reachable(requires_cborg: None) -> None:
    """The CBORG endpoint responds to an authenticated request with the .env key.

    This is the low-level check: credentials and base URL are valid and the
    gateway is reachable, independent of the recommendation pipeline.
    """
    client = OpenAI(
        api_key=os.environ["CBORG_KEY"],
        base_url=os.environ["CBORG_BASE_URL"],
    )
    model_ids = [m.id for m in client.models.list().data]
    assert model_ids, "CBORG returned no models"
    assert CBORG_MODEL in model_ids, (
        f"{CBORG_MODEL} not offered by CBORG; available sample: {model_ids[:8]}"
    )


@pytest.mark.integration
@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_cborg_pipeline_returns_field_guidance(requires_cborg: None) -> None:
    """The recommendation pipeline runs end to end over CBORG and returns valid output.

    This is the field-guidance task: each suggestion names a metadata field the
    submitter should populate, with a reason. It does not fill in a value, so
    this asserts field_name and reason are present and does NOT assert a
    non-empty value (confirmed empty for this task with both gpt-4o-mini and
    gpt-5).
    """
    submission = _load_fixture("full_submission.json")
    client = LLMClient(access_provider="cborg", model=CBORG_MODEL)

    result = run_recommendation_pipeline(submission_object=submission, llm_client=client)

    assert result.metadata_fields, "Expected at least one metadata field suggestion"
    for field in result.metadata_fields:
        assert field.field_name, "field_name must not be empty"
        assert field.reason, "reason must not be empty"

    assert result.model == CBORG_MODEL
    assert result.access_provider == "cborg"
