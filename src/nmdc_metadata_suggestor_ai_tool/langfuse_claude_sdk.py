"""Re-exports from claude_agent_sdk, with tracing instrumentation applied first.

Import claude_agent_sdk symbols from here instead of directly from the package.
The OpenInference instrumentor must wrap claude_agent_sdk.query.query *in place*
before any local name binds to it; this module guarantees that order.
IE - setup_tracing() must run BEFORE importing anything from claude_agent_sdk
"""

from nmdc_metadata_suggestor_ai_tool.tracing import langfuse_client, setup_tracing

if langfuse_client is not None:
    setup_tracing()

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    query,
)

__all__ = [
    "AssistantMessage",
    "ClaudeAgentOptions",
    "ResultMessage",
    "SystemMessage",
    "query",
]
