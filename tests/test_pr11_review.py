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
from pathlib import Path

from nmdc_metadata_suggestor.llm_client import LLMClient


class TestAddMessageRoleConsistency:
    """add_message() signature, callers, and docstring should agree on 'role'."""

    def test_callers_match_signature(self):
        """If add_message() doesn't accept 'role', no caller should pass it.
        If it does accept 'role', callers are free to use it."""
        sig = inspect.signature(LLMClient.add_message)
        accepts_role = "role" in sig.parameters
        if not accepts_role:
            # Scan test_llm_client.py for callers still passing role=
            test_source = Path(__file__).parent / "test_llm_client.py"
            if test_source.exists():
                content = test_source.read_text()
                assert "role=" not in content, (
                    "test_llm_client.py passes role= to add_message() "
                    "but the signature doesn't accept it"
                )

    def test_docstring_matches_signature(self):
        """Docstring and signature should agree on whether 'role' exists."""
        sig = inspect.signature(LLMClient.add_message)
        if "role" in (LLMClient.add_message.__doc__ or ""):
            assert "role" in sig.parameters, (
                "Docstring documents a 'role' parameter that doesn't exist in the signature"
            )


class TestGeneratePnnlForwardsParams:
    """_generate_pnnl should forward max_tokens to the API.

    Note: GPT-5+ and o3/o4 do not support temperature
    (https://community.openai.com/t/gpt-5-models-temperature/1337957),
    so we only assert max_tokens is forwarded.
    """

    def test_generate_pnnl_accepts_max_tokens(self):
        """generate() accepts max_tokens — _generate_pnnl should too."""
        sig = inspect.signature(LLMClient._generate_pnnl)
        params = set(sig.parameters.keys()) - {"self"}
        assert "max_tokens" in params, "_generate_pnnl ignores max_tokens"


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
