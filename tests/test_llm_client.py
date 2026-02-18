"""Tests for the Vertex AI LLM client (Gemini and Claude)."""

import os

import pytest

from nmdc_metadata_suggestor.llm_client import LLMClient

_has_gcp_credentials = (
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    or os.environ.get("GCLOUD_PROJECT")
    or os.environ.get("CLOUDSDK_CONFIG")
)

requires_credentials = pytest.mark.skipif(
    not _has_gcp_credentials,
    reason="GCP credentials not available",
)


def test_invalid_provider_raises() -> None:
    with pytest.raises(ValueError, match="Unknown provider"):
        LLMClient(provider="openai")


@requires_credentials
def test_default_provider_is_gemini() -> None:
    client = LLMClient()
    assert client.provider == "gemini"


@requires_credentials
def test_gemini_generate() -> None:
    client = LLMClient(provider="gemini")
    response = client.generate(
        "Reply with exactly: hello",
        model="gemini-2.0-flash",
        max_tokens=50,
    )
    assert isinstance(response, str)
    assert len(response) > 0


@requires_credentials
def test_gemini_generate_with_system() -> None:
    client = LLMClient(provider="gemini")
    response = client.generate(
        "What are you?",
        model="gemini-2.0-flash",
        system="You are a biome classification assistant. Always mention ENVO.",
        max_tokens=200,
    )
    assert isinstance(response, str)
    assert len(response) > 0


@requires_credentials
def test_claude_generate() -> None:
    client = LLMClient(provider="claude")
    response = client.generate(
        "Reply with exactly: hello",
        model="haiku",
        max_tokens=50,
    )
    assert isinstance(response, str)
    assert len(response) > 0


@requires_credentials
def test_claude_generate_with_system() -> None:
    client = LLMClient(provider="claude")
    response = client.generate(
        "What are you?",
        model="haiku",
        system="You are a biome classification assistant. Always mention ENVO.",
        max_tokens=200,
    )
    assert isinstance(response, str)
    assert len(response) > 0
