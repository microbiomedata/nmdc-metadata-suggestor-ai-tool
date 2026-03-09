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


def test_invalid_access_provider_raises() -> None:
    with pytest.raises(ValueError, match="Unknown access_provider"):
        LLMClient(access_provider="invalid")


@requires_credentials
def test_default_provider_is_gemini() -> None:
    client = LLMClient()
    assert client.model == "gemini-2.0-flash"


@requires_credentials
def test_gemini_generate() -> None:
    client = LLMClient(model="gemini-2.0-flash")
    client.add_message(text="Reply with exactly: hello")
    response = client.generate(
        model="gemini-2.0-flash",
        max_tokens=50,
    )
    assert isinstance(response, str)
    assert len(response) > 0


@requires_credentials
def test_gemini_generate_with_system() -> None:
    client = LLMClient(model="gemini-2.0-flash")
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


@requires_credentials
def test_claude_generate() -> None:
    client = LLMClient(model="claude-opus-4-6")
    client.add_message(text="Reply with exactly: hello")
    response = client.generate(
        model="haiku",
        max_tokens=50,
    )
    assert isinstance(response, str)
    assert len(response) > 0


@requires_credentials
def test_claude_generate_with_system() -> None:
    client = LLMClient(model="claude-opus-4-6")
    client.add_message(
        text="You are a biome classification assistant. Always mention ENVO.",
    )
    client.add_message(text="What are you?")
    response = client.generate(
        model="haiku",
        max_tokens=200,
    )
    assert isinstance(response, str)
    assert len(response) > 0
