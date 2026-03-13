from types import SimpleNamespace

import pytest

from nmdc_metadata_suggestor import recommendation_pipeline


class _FakeLLMClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.model = "gpt-5-project"
        self.access_provider = "pnnl"

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
    client = _FakeLLMClient(
        response='{"metadata_fields":[{"field_name":"env_broad_scale","reason":"Required for submission.","value":"soil"}]}'
    )

    result = recommendation_pipeline.run_recommendation_pipeline(
        doi="10.1234/example",
        llm_client=client,
        mixis_extensions=["SoilInterface"],
    )

    assert result.metadata_fields[0].field_name == "env_broad_scale"
    assert result.model == "gpt-5-project"
    assert result.access_provider == "pnnl"


def test_run_recommendation_pipeline_raises_for_invalid_output_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_common_inputs(monkeypatch)
    client = _FakeLLMClient(response='{"metadata_fields":[{"field_name":"env_broad_scale","value":"soil"}]}')

    with pytest.raises(ValueError, match="expected output schema"):
        recommendation_pipeline.run_recommendation_pipeline(
            doi="10.1234/example",
            llm_client=client,
            mixis_extensions=["SoilInterface"],
        )