import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from nmdc_metadata_suggestor_ai_tool import recommendation_pipeline
from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient


def load_sample_submission_object() -> dict:
    """Load a sample submission object from the test fixtures."""
    import json

    sample_path = Path(__file__).parent / "fixtures" / "test_submission.json"
    with open(sample_path) as f:
        return json.load(f)


class _FakeLLMClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.model = "gpt-5-project"
        self.access_provider = "pnnl"
        # provide a `.client` with the shape expected by ConversationManager
        # for the PNNL provider: `client.responses.create(...)` returning
        # an object with `output_text`.
        # Support both `responses.create(...).output_text` and
        # `responses.parse(...).output_parsed` shapes so tests work with the
        # real ConversationManager implementation which may call `parse`.
        self.client = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(output_text=self.response),
                parse=lambda **kwargs: SimpleNamespace(output_parsed=self.response),
            )
        )

    def add_schema_context(self, schema: str) -> None:
        _ = schema

    def add_message(self, text: str, pdf_files: list[str] | None = None) -> None:
        _ = (text, pdf_files)

    def generate(self, max_tokens: int | None = None) -> str:
        _ = max_tokens
        return self.response


def _stub_common_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        recommendation_pipeline,
        "get_doi_description_or_abstract",
        lambda **_: SimpleNamespace(context="Test abstract", publication_urls=[]),
    )
    monkeypatch.setattr(
        recommendation_pipeline.SchemaContextBuilder,
        "format_multi_interface_context",
        lambda *args, **kwargs: "schema context",
    )


def test_run_recommendation_pipeline_validates_and_returns_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_common_inputs(monkeypatch)
    sample_submission_object = load_sample_submission_object()
    client = _FakeLLMClient(
        response=(
            '{"metadata_fields":[{"field_name":"env_broad_scale",'
            '"reason":"Required for submission.","value":"soil"}]}'
        )
    )

    result = recommendation_pipeline.run_recommendation_pipeline(
        submission_object=sample_submission_object,
        llm_client=client,
    )

    assert result.metadata_fields[0].field_name == "env_broad_scale"
    assert result.model == "gpt-5-project"
    assert result.access_provider == "pnnl"


def test_run_recommendation_pipeline_raises_for_invalid_output_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_common_inputs(monkeypatch)
    sample_submission_object = load_sample_submission_object()
    client = _FakeLLMClient(
        response='{"metadata_fields":[{"field_name":"env_broad_scale","value":"soil"}]}'
    )

    with pytest.raises(ValueError, match="expected output schema"):
        recommendation_pipeline.run_recommendation_pipeline(
            submission_object=sample_submission_object,
            llm_client=client,
        )


def test_run_recommendation_pipeline(requires_credentials: None) -> None:
    start = time.time_ns()
    sample_submission_object = load_sample_submission_object()
    llm_client = LLMClient(access_provider="gcp")
    recommended_metadata = recommendation_pipeline.run_recommendation_pipeline(
        submission_object=sample_submission_object, llm_client=llm_client
    )
    print(recommended_metadata.model_dump())
    end = time.time_ns()
    print(f"Pipeline execution time: {(end - start) / 1e9:.2f} seconds")
