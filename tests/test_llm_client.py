"""Tests for the Vertex AI LLM client (Gemini and Claude)."""

from typing import Any

import pytest

from nmdc_metadata_suggestor_ai_tool.llm_client import (
    DEFAULT_MAX_TOKENS_BY_PROVIDER,
    ConversationManager,
    LLMClient,
)
from nmdc_metadata_suggestor_ai_tool.system_prompt import system_prompt


def test_invalid_access_provider_raises() -> None:
    with pytest.raises(ValueError, match="Unknown access_provider"):
        LLMClient(access_provider="invalid")


def test_generate_uses_pnnl_default_max_tokens_when_not_provided(monkeypatch: Any) -> None:
    client = LLMClient.__new__(LLMClient)
    client.access_provider = "pnnl"
    client.model = "gpt-5-project"
    conversation = ConversationManager(llm_client=client, system_prompt=system_prompt)

    def _fake_generate_pnnl(*, max_tokens: int, system_prompt: str) -> str:
        assert max_tokens == DEFAULT_MAX_TOKENS_BY_PROVIDER["pnnl"]
        assert system_prompt == system_prompt
        return "ok"

    monkeypatch.setattr(conversation, "_generate_openai", _fake_generate_pnnl)
    assert conversation.generate() == "ok"


def test_generate_uses_cborg_default_max_tokens_when_not_provided(monkeypatch: Any) -> None:
    client = LLMClient.__new__(LLMClient)
    client.access_provider = "cborg"
    client.model = "gemini-2.5-flash"
    conversation = ConversationManager(llm_client=client, system_prompt=system_prompt)

    def _fake_generate_cborg(*, max_tokens: int, system_prompt: str) -> str:
        assert max_tokens == DEFAULT_MAX_TOKENS_BY_PROVIDER["cborg"]
        assert system_prompt == system_prompt
        return "ok"

    monkeypatch.setattr(conversation, "_generate_openai", _fake_generate_cborg)
    assert conversation.generate() == "ok"


def test_generate_uses_gcp_default_max_tokens_when_not_provided(monkeypatch: Any) -> None:
    client = LLMClient.__new__(LLMClient)
    client.access_provider = "gcp"
    client.model = "gemini-2.5-flash"
    conversation = ConversationManager(llm_client=client, system_prompt=system_prompt)

    def _fake_generate_gcp(*, max_tokens: int, temperature: float, system_prompt: str) -> str:
        assert max_tokens == DEFAULT_MAX_TOKENS_BY_PROVIDER["gcp"]
        assert temperature == 0.1
        assert system_prompt == system_prompt
        return "ok"

    monkeypatch.setattr(conversation, "_generate_gcp", _fake_generate_gcp)
    assert conversation.generate(gemini_temperature=0.1) == "ok"


def test_generate_uses_explicit_max_tokens_override(monkeypatch: Any) -> None:
    client = LLMClient.__new__(LLMClient)
    client.access_provider = "gcp"
    client.model = "gemini-2.5-flash"
    conversation = ConversationManager(llm_client=client, system_prompt=system_prompt)

    def _fake_generate_gcp(*, max_tokens: int, temperature: float, system_prompt: str) -> str:
        assert max_tokens == 2048
        assert temperature == 0.4
        assert system_prompt == system_prompt
        return "ok"

    monkeypatch.setattr(conversation, "_generate_gcp", _fake_generate_gcp)
    assert conversation.generate(max_tokens=2048) == "ok"


def test_default_provider_is_gemini(requires_credentials: None) -> None:
    client = LLMClient(access_provider="gcp")
    assert client.model == "gemini-2.5-flash"


def test_cborg_client_reads_env_at_initialization_time(monkeypatch: Any) -> None:
    monkeypatch.setenv("CBORG_KEY", "test-key")
    monkeypatch.setenv("CBORG_BASE_URL", "https://api.cborg.lbl.gov")

    client = LLMClient(access_provider="cborg")

    assert client.model == "gemini-2.5-flash"
    assert str(client.client.base_url).rstrip("/") == "https://api.cborg.lbl.gov"


def test_cborg_add_message_uses_openai_payload_shape() -> None:
    client = LLMClient.__new__(LLMClient)
    client.access_provider = "cborg"
    client.model = "gemini-2.5-flash"
    conversation = ConversationManager(llm_client=client, system_prompt=system_prompt)

    conversation.add_message(text="hello")

    assert conversation.messages == [{"role": "user", "content": "hello"}]


def test_gemini_generate(requires_credentials: None) -> None:
    client = LLMClient(access_provider="gcp", model="gemini-2.5-flash")
    conversation = ConversationManager(llm_client=client, system_prompt=system_prompt)
    conversation.add_message(text="Reply with exactly: hello")
    response = conversation.generate(
        model="gemini-2.5-flash",
        max_tokens=50,
    )
    assert isinstance(response, str)
    assert len(response) > 0


def test_gemini_generate_with_system(requires_credentials: None) -> None:
    client = LLMClient(access_provider="gcp", model="gemini-2.5-flash")
    conversation = ConversationManager(llm_client=client, system_prompt=system_prompt)
    conversation.add_message(
        text="You are a biome classification assistant. Always mention ENVO.",
    )
    conversation.add_message(text="What are you?")
    response = conversation.generate(
        model="gemini-2.5-flash",
        max_tokens=200,
    )
    assert isinstance(response, str)
    assert len(response) > 0
