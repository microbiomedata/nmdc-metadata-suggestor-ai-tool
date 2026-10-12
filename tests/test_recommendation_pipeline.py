import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import nmdc_metadata_suggestor_ai_tool.recommendation_pipeline as recommendation_pipeline
import nmdc_metadata_suggestor_ai_tool.utils.build_submission_context as build_submission_context
from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient


def load_sample_submission_object() -> dict[str, Any]:
    """Load a sample submission object from the test fixtures."""
    import json

    sample_path = Path(__file__).parent / "fixtures" / "test_submission.json"
    with open(sample_path) as f:
        data: dict[str, Any] = json.load(f)
        return data


# Duck-types LLMClient for the pipeline's purposes. Call sites cast rather than
# subclass, so the fake does not inherit LLMClient's credential setup.
class _FakeLLMClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.model = "gpt-5-project"
        self.access_provider = "pnnl"
        # provide a `.client` with the shape expected by ConversationManager
        # for the PNNL provider: `responses.parse(...).output_parsed` returning
        # an object with `output_parsed`
        self.client = SimpleNamespace(
            responses=SimpleNamespace(
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
        build_submission_context,
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
        llm_client=cast(LLMClient, client),
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
            llm_client=cast(LLMClient, client),
        )


_VALID_RESPONSE = (
    '{"metadata_fields":[{"field_name":"env_broad_scale",'
    '"reason":"Required for submission.","value":"soil"}]}'
)


def _spy_on_schema_context(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the network abstract lookup and capture the class names that the
    pipeline hands to ``format_multi_interface_context``.

    Note: the other pipeline tests stub ``format_multi_interface_context`` via
    ``_stub_common_inputs`` and discard its argument, so they never observe what
    ``interface_name`` resolves to. This spy records that argument instead.
    """
    monkeypatch.setattr(
        build_submission_context,
        "get_doi_description_or_abstract",
        lambda **_: SimpleNamespace(context="Test abstract", publication_urls=[]),
    )
    captured: dict = {}

    def _record(self: object, class_names: list[str]) -> str:
        captured["class_names"] = class_names
        return "schema context"

    monkeypatch.setattr(
        recommendation_pipeline.SchemaContextBuilder,
        "format_multi_interface_context",
        _record,
    )
    return captured


def test_interface_name_resolves_to_interface_class_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A supplied ``interface_name`` must resolve to the same interface-class
    vocabulary as the submission-object path (``"soil"`` -> ``["SoilInterface"]``).

    Reveals the bug: the pipeline does ``list(interface_name)``, which splits the
    string into characters (``["s", "o", "i", "l"]``) instead of normalizing it.
    """
    captured = _spy_on_schema_context(monkeypatch)
    client = _FakeLLMClient(response=_VALID_RESPONSE)

    recommendation_pipeline.run_recommendation_pipeline(
        submission_object=load_sample_submission_object(),
        llm_client=cast(LLMClient, client),
        interface_name="soil",
    )

    assert captured["class_names"] == ["SoilInterface"]


def test_interface_name_accepts_interface_class_name_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parser was extended to accept enum names too, so passing the class
    name directly should also resolve to ``["SoilInterface"]``.

    Reveals the bug: ``list("SoilInterface")`` yields a list of 13 characters.
    """
    captured = _spy_on_schema_context(monkeypatch)
    client = _FakeLLMClient(response=_VALID_RESPONSE)

    recommendation_pipeline.run_recommendation_pipeline(
        submission_object=load_sample_submission_object(),
        llm_client=cast(LLMClient, client),
        interface_name="SoilInterface",
    )

    assert captured["class_names"] == ["SoilInterface"]


def test_interface_name_does_not_crash_schema_context_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end guard: supplying ``interface_name`` must not raise.

    ``format_multi_interface_context`` is deliberately NOT stubbed here, so the
    real ``SchemaContextBuilder`` runs. Reveals the bug: the character-split feeds
    invalid class names into the builder, which raises
    ``ValueError: Unknown class: s``.
    """
    monkeypatch.setattr(
        build_submission_context,
        "get_doi_description_or_abstract",
        lambda **_: SimpleNamespace(context="Test abstract", publication_urls=[]),
    )
    client = _FakeLLMClient(response=_VALID_RESPONSE)

    result = recommendation_pipeline.run_recommendation_pipeline(
        submission_object=load_sample_submission_object(),
        llm_client=cast(LLMClient, client),
        interface_name="soil",
    )

    assert result.metadata_fields[0].field_name == "env_broad_scale"


def test_interface_name_overrides_submission_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A supplied ``interface_name`` takes precedence over the submission's own
    package. The sample submission is a soil package; passing ``"water"`` should
    drive the schema context to ``["WaterInterface"]``, not the submission's soil.

    Reveals the bug: ``list("water")`` yields ``["w", "a", "t", "e", "r"]``.
    """
    captured = _spy_on_schema_context(monkeypatch)
    client = _FakeLLMClient(response=_VALID_RESPONSE)

    recommendation_pipeline.run_recommendation_pipeline(
        submission_object=load_sample_submission_object(),
        llm_client=cast(LLMClient, client),
        interface_name="water",
    )

    assert captured["class_names"] == ["WaterInterface"]


@pytest.mark.parametrize("interface_name", [None, ""])
def test_absent_interface_name_falls_back_to_submission_package(
    monkeypatch: pytest.MonkeyPatch, interface_name: str | None
) -> None:
    """When ``interface_name`` is absent or empty, the pipeline falls back to the
    interfaces parsed from the submission object (soil for the sample fixture).

    Passes today; a regression guard so the fix preserves the fallback path.
    """
    captured = _spy_on_schema_context(monkeypatch)
    client = _FakeLLMClient(response=_VALID_RESPONSE)

    recommendation_pipeline.run_recommendation_pipeline(
        submission_object=load_sample_submission_object(),
        llm_client=cast(LLMClient, client),
        interface_name=interface_name,
    )

    assert captured["class_names"] == ["SoilInterface"]


def test_run_recommendation_pipeline(requires_credentials: None) -> None:
    start = time.time_ns()
    sample_submission_object = load_sample_submission_object()
    llm_client = LLMClient(access_provider="gcp")
    recommended_metadata = recommendation_pipeline.run_recommendation_pipeline(
        submission_object=sample_submission_object, llm_client=llm_client
    )
    print(recommended_metadata.model_dump())
    # assert there is data in the recommended_metadata obj
    assert recommended_metadata.metadata_fields is not None
    assert len(recommended_metadata.metadata_fields) > 0
    end = time.time_ns()
    print(f"Pipeline execution time: {(end - start) / 1e9:.2f} seconds")
