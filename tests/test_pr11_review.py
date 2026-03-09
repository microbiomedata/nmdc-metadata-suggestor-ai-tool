"""Tests for issues found during PR #11 review.

These tests fail on the current branch, demonstrating the bugs.
When the bugs are fixed, these tests should pass.

Note: the signature-based test for _generate_pnnl could alternatively be written
as a behavioral test using monkeypatch to assert that generate() forwards
max_tokens and temperature to the underlying method. That approach is more
resilient to implementation choices like **kwargs. We chose the simpler
signature check here for readability.
"""

import inspect

from nmdc_metadata_suggestor.llm_client import LLMClient


class TestAddMessageAcceptsRole:
    """add_message() should accept a role parameter, or callers should stop passing it."""

    def test_add_message_accepts_role_kwarg(self):
        """test_gemini_generate (line 35) passes role='user' to add_message,
        but add_message doesn't accept it. Whichever side is wrong, they need to agree."""
        client = LLMClient.__new__(LLMClient)
        client.access_provider = "gcp"
        client.messages = []
        # This should not raise
        client.add_message(role="user", text="hello")

    def test_docstring_matches_signature(self):
        """Docstring mentions 'role' parameter but signature only has 'text' and 'pdf_files'."""
        sig = inspect.signature(LLMClient.add_message)
        if "role" in (LLMClient.add_message.__doc__ or ""):
            assert "role" in sig.parameters, (
                "Docstring documents a 'role' parameter that doesn't exist in the signature"
            )


class TestGeneratePnnlForwardsParams:
    """_generate_pnnl should forward max_tokens and temperature to the API."""

    def test_generate_pnnl_accepts_tuning_params(self):
        """The OpenAI Responses API supports max_tokens and temperature,
        but _generate_pnnl doesn't forward them from generate()."""
        sig = inspect.signature(LLMClient._generate_pnnl)
        params = set(sig.parameters.keys()) - {"self"}
        assert "max_tokens" in params, "_generate_pnnl ignores max_tokens"
        assert "temperature" in params, "_generate_pnnl ignores temperature"


class TestNoDeadCode:
    """Unused code should be removed."""

    def test_claude_models_used_or_removed(self):
        """CLAUDE_MODELS dict is defined but no code path references it
        after the Claude provider was removed."""
        source = inspect.getsource(LLMClient.generate)
        has_claude_path = "claude" in source.lower()
        has_claude_dict = hasattr(
            __import__("nmdc_metadata_suggestor.llm_client", fromlist=["CLAUDE_MODELS"]),
            "CLAUDE_MODELS",
        )
        if has_claude_dict:
            assert has_claude_path, (
                "CLAUDE_MODELS is defined but generate() never uses it — remove or implement"
            )
