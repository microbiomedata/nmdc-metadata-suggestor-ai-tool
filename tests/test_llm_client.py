"""Tests for LLM client module."""

import pytest

from nmdc_metadata_suggestor.llm_client import LLMClient


def test_llm_client_initialization() -> None:
    """Test LLM client initialization."""
    client = LLMClient(provider="openai")
    assert client.provider == "openai"


def test_unsupported_provider() -> None:
    """Test that unsupported provider raises ValueError."""
    with pytest.raises(ValueError, match="Unsupported provider"):
        LLMClient(provider="invalid_provider")


def test_generate_without_api_key() -> None:
    """Test that generate raises error without API key."""
    client = LLMClient(provider="openai")
    with pytest.raises(RuntimeError, match="LLM client not initialized"):
        client.generate("test prompt")
