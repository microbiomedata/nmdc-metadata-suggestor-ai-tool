"""Langfuse tracing setup (SDK v4).

Call ``setup_tracing()`` once at application startup — before any claude-agent-sdk
calls — to activate OpenTelemetry-based instrumentation and route spans to Langfuse.

When LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are absent the function is a no-op
and the ``observe`` re-export becomes a transparent pass-through decorator, so the
rest of the code needs no guards.

Environment variables (set in .env or the shell):
    LANGFUSE_PUBLIC_KEY   pk-lf-...
    LANGFUSE_SECRET_KEY   sk-lf-...
    LANGFUSE_BASE_URL     https://cloud.langfuse.com  (or self-hosted URL)
"""

import inspect
import logging
import os
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

langfuse_enabled = bool(
    os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
)

if langfuse_enabled:
    try:
        from langfuse import Langfuse, get_client
        from langfuse import observe as _lf_observe
        from openinference.instrumentation.claude_agent_sdk import ClaudeAgentSDKInstrumentor

        langfuse_environment = os.environ.get("LANGFUSE_TRACING_ENVIRONMENT")
        # check if the tracing environment is set, default to 'unknown' if not
        if langfuse_environment not in ["production", "development", "local"]:
            langfuse_environment = "unknown"
        logger.debug(f"Langfuse tracing environment set to: {langfuse_environment}")
        Langfuse(environment=langfuse_environment)
        langfuse = get_client()
        observe = _lf_observe

        def setup_tracing() -> None:
            """Activate ClaudeAgentSDK → Langfuse instrumentation.

            Must be called once before the first ``query()`` call.
            """
            ClaudeAgentSDKInstrumentor().instrument()
            logger.debug("Langfuse tracing enabled (ClaudeAgentSDKInstrumentor active)")

        logger.debug("Langfuse tracing available; call setup_tracing() to activate")
    except Exception:
        logger.warning("Langfuse/openinference import failed; tracing disabled", exc_info=True)
        langfuse_enabled = False

if not langfuse_enabled:
    langfuse = None  # type: ignore[assignment]

    def setup_tracing() -> None:  # type: ignore[misc]
        """No-op when Langfuse credentials are not configured."""

    def observe(  # type: ignore[misc]  # noqa: UP047
        func: F | None = None,
        *,
        name: str | None = None,
        as_type: str | None = None,
        capture_input: bool = True,
        capture_output: bool = True,
    ) -> Any:
        """No-op stand-in when Langfuse is not configured."""

        def decorator(f: F) -> F:
            if inspect.iscoroutinefunction(f):

                @wraps(f)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    return await f(*args, **kwargs)

                return async_wrapper  # type: ignore[return-value]

            @wraps(f)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return f(*args, **kwargs)

            return wrapper  # type: ignore[return-value]

        if func is not None:
            return decorator(func)
        return decorator


def flush() -> None:
    """Flush pending Langfuse events (call before process exit in short-lived scripts)."""
    if langfuse_enabled and langfuse is not None:
        langfuse.flush()
