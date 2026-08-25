# Agent permissions

`ConversationManager.agentic()` runs the env-triad skill through the Claude Agent SDK, which
spawns the Claude Code CLI as a subprocess. Under the SDK's default permission mode `Bash`
needs interactive approval — and a headless run has nobody to approve it.

The effect was not a crash but something quieter. The agent asked to run its ontology
lookups, was denied, tried once more via a temp script, was denied again, and then answered
from the value sets in its prompt alone. Traces showed runs where 95 of 104 observations were
denials and the suggestions still looked plausible. In one 100-sample run the agent made
exactly two attempts to reach ENVO, both denied, and produced all 300 suggestions without
consulting the ontology once.

That is the whole ENVO-expansion tier silently not running.

## How it is configured

Two files, because they have different audiences.

`.claude/settings.json` is read by **every Claude Code session in this repo**, the SDK
subprocess and a person's editor alike. It carries the allowlist the skill needs, plus the
denies that are right for anyone: nothing should be reading `.env` or the GCP credentials.

`.claude/agent-settings.json` is loaded **only** by `agentic()`, via
`ClaudeAgentOptions.settings`. It carries the restrictions that suit an unattended agent and
would be obstructive to a person — no network fetches, no repo mutation, no dependency
changes.

That split was learned the hard way: putting `Bash(git commit:*)` in the project file blocked
the maintainer session that had just written it. A shared settings file is not the place for
agent-specific restraint.

`agentic()` passes both `setting_sources=["project"]` and `settings=<agent file>`. The first
is required at all — `setting_sources` defaults to `None`, so without it the SDK ignores
project settings entirely and the allowlist does nothing.

## What is allowed, and the honest caveat

The allowlist covers what the skill actually needs: running Python against this package for
`envo` and `schema_context` lookups, plus read-only shell utilities for finding files.

`Bash(uv run python:*)` and `Bash(python3:*)` are **arbitrary code execution** in practice.
Prefix matching cannot express "only ENVO lookups", so a narrow allowlist would need a narrow
command surface — a small CLI with a fixed verb set — rather than a general interpreter. That
is a real tradeoff, not a solved problem, and worth revisiting if this runs anywhere less
trusted than a maintainer's machine or a controlled service.

## What is denied, and why those specifically

| Denied | Reason |
|---|---|
| `.env`, `.env.*`, `*credential*`, `*secret*`, `gcp_credentials.json` | The repo's `.env` holds Vertex, Langfuse, PNNL and CBORG keys. The agent has no reason to read any of them. |
| `curl`, `wget`, `nc`, `ssh` | Ontology access goes through oaklib's cached adapter. Nothing in the request path should fetch over the network, and issue #109 asked specifically that the agent not be pointed at URLs. **Agent file only** — maintainers legitimately curl the NMDC and Langfuse APIs. |
| `rm`, `git push`, `git commit`, `git reset`, `git checkout`, `gh` | The agent suggests metadata. It does not need to mutate the repo or reach GitHub. **Agent file only** — a person needs these. |
| `uv add`, `uv remove`, `pip install` | Dependencies are reviewed changes, not something an agent decides mid-run. **Agent file only.** |

## Checking it works

A run that is being blocked is visible without reading the trace by hand:
`ConversationManager.run_health` counts `permission_denials` off the `ResultMessage`, groups
them by tool, logs a warning, and attaches the counts to the Langfuse span. A healthy run
reports zero.
