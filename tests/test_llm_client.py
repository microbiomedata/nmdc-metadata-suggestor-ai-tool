"""Tests for the Vertex AI LLM client (Gemini and Claude)."""

import os
from pathlib import Path
from typing import Any

import pytest

from nmdc_metadata_suggestor.llm_client import DEFAULT_MAX_TOKENS_BY_PROVIDER, LLMClient

_repo_root = Path(__file__).resolve().parent.parent
_default_credentials_file = _repo_root / "gcp_credentials.json"

if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and _default_credentials_file.exists():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_default_credentials_file)

_has_gcp_credentials = bool(
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    or os.environ.get("GCLOUD_PROJECT")
    or os.environ.get("CLOUDSDK_CONFIG")
    or _default_credentials_file.exists()
)

requires_credentials = pytest.mark.skipif(
    not _has_gcp_credentials,
    reason="GCP credentials not available",
)


def test_invalid_access_provider_raises() -> None:
    with pytest.raises(ValueError, match="Unknown access_provider"):
        LLMClient(access_provider="invalid")


def test_generate_uses_pnnl_default_max_tokens_when_not_provided(monkeypatch: Any) -> None:
    client = LLMClient.__new__(LLMClient)
    client.access_provider = "pnnl"
    client.model = "gpt-5-project"

    def _fake_generate_pnnl(*, max_tokens: int) -> str:
        assert max_tokens == DEFAULT_MAX_TOKENS_BY_PROVIDER["pnnl"]
        return "ok"

    monkeypatch.setattr(client, "_generate_pnnl", _fake_generate_pnnl)
    assert client.generate() == "ok"


def test_generate_uses_gcp_default_max_tokens_when_not_provided(monkeypatch: Any) -> None:
    client = LLMClient.__new__(LLMClient)
    client.access_provider = "gcp"
    client.model = "gemini-2.0-flash"

    def _fake_generate_gcp(*, max_tokens: int, temperature: float) -> str:
        assert max_tokens == DEFAULT_MAX_TOKENS_BY_PROVIDER["gcp"]
        assert temperature == 0.1
        return "ok"

    monkeypatch.setattr(client, "_generate_gcp", _fake_generate_gcp)
    assert client.generate(gemini_temperature=0.1) == "ok"


def test_generate_uses_explicit_max_tokens_override(monkeypatch: Any) -> None:
    client = LLMClient.__new__(LLMClient)
    client.access_provider = "gcp"
    client.model = "gemini-2.0-flash"

    def _fake_generate_gcp(*, max_tokens: int, temperature: float) -> str:
        assert max_tokens == 2048
        assert temperature == 0.4
        return "ok"

    monkeypatch.setattr(client, "_generate_gcp", _fake_generate_gcp)
    assert client.generate(max_tokens=2048) == "ok"


@requires_credentials
def test_default_provider_is_gemini() -> None:
    client = LLMClient(access_provider="gcp")
    assert client.model == "gemini-2.0-flash"


@requires_credentials
def test_gemini_generate() -> None:
    client = LLMClient(access_provider="gcp", model="gemini-2.0-flash")
    client.add_message(text="Reply with exactly: hello")
    response = client.generate(
        model="gemini-2.0-flash",
        max_tokens=50,
    )
    assert isinstance(response, str)
    assert len(response) > 0


@requires_credentials
def test_gemini_generate_with_system() -> None:
    client = LLMClient(access_provider="gcp", model="gemini-2.0-flash")
    client.add_message(
        text="You are a biome classification assistant. Always mention ENVO.",
    )
    client.add_message(text="What are you?")
    response = client.generate(
        model="gemini-2.0-flash",
        max_tokens=200,
    )
    assert isinstance(response, str)
    assert len(response) > 0
