"""Permission policy for the unattended Claude Agent SDK run.

Declared in Python so the same policy applies on every machine without depending on
`.claude/settings.json` (which needs the per-user workspace trust flag to take effect)
or on Claude Code being installed at all. Wired into ``ClaudeAgentOptions`` as a
``PreToolUse`` hook -- see ``llm_client.py``.
"""

import re

from nmdc_metadata_suggestor_ai_tool.langfuse_claude_sdk import (
    AsyncHookJSONOutput,
    HookContext,
    HookInput,
    PreToolUseHookInput,
    SyncHookJSONOutput,
)

# The tool the agent calls to hand back its final answer.
STRUCTURED_OUTPUT_TOOL = "StructuredOutput"

ALLOWED_BASH_PREFIXES = (
    "uv run python",
    "uv run pytest",
    "python3",
    "grep",
    "rg",
    "find",
    "ls",
    "cat",
    "head",
    "wc",
)
DENIED_BASH_PREFIXES = (
    "curl",
    "wget",
    "nc",
    "ssh",
    "rm",
    "git push",
    "git commit",
    "git reset",
    "git checkout",
    "gh",
    "uv add",
    "uv remove",
    "pip install",
)
DENIED_READ_PATTERNS = (
    re.compile(r"(^|/)\.env(\..*)?$"),
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"gcp_credentials\.json$"),
)
ALWAYS_ALLOWED_TOOLS = {"Read", "Grep", "Glob", "Skill", STRUCTURED_OUTPUT_TOOL}


def _bash_matches(command: str, prefixes: tuple[str, ...]) -> bool:
    return any(command == p or command.startswith(p + " ") for p in prefixes)


async def pretool_permission_gate(
    input_data: HookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> AsyncHookJSONOutput | SyncHookJSONOutput:
    """PreToolUse hook: block disallowed tools/commands, allow everything else."""
    if input_data["hook_event_name"] != "PreToolUse":
        return {}
    pretool: PreToolUseHookInput = input_data  # type: ignore[assignment]
    tool_name = pretool["tool_name"]
    tool_input = pretool["tool_input"] or {}

    if tool_name == "Bash":
        command = (tool_input.get("command") or "").strip()
        head = command.split(" ", 1)[0] if command else ""
        if _bash_matches(command, DENIED_BASH_PREFIXES):
            return {"decision": "block", "reason": f"bash denied: {head}"}
        if not _bash_matches(command, ALLOWED_BASH_PREFIXES):
            return {"decision": "block", "reason": f"bash not in allowlist: {head}"}
    elif tool_name == "Read":
        path = tool_input.get("file_path", "")
        if any(pattern.search(path) for pattern in DENIED_READ_PATTERNS):
            return {"decision": "block", "reason": f"read denied: {path}"}
    elif tool_name not in ALWAYS_ALLOWED_TOOLS:
        return {"decision": "block", "reason": f"tool not allowed: {tool_name}"}
    return {}
