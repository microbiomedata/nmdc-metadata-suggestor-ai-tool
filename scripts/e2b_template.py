"""Build the `nmdc-agent` E2B template used by the E2B demos.

The template bakes in everything that is slow or large, so a run only uploads its
inputs (and optionally a fresh wheel of the package):
  - Python 3.12 + uv
  - all locked runtime dependencies, in /opt/nmdc/.venv
  - the package itself, plus .claude/skills/ (the agent reads skills from its cwd)
  - the Claude Code CLI, which claude-agent-sdk ships as a bundled binary
  - a warmed ENVO cache, so env-triad lookups don't download the ontology per run

No credentials go into the template. Every sandbox started from it can read all of
it, so .env, service-account files, and .claude/settings.local.json are excluded.

Usage:
    uv run --extra e2b python scripts/e2b_template.py           # build/update nmdc-agent
    uv run --extra e2b python scripts/e2b_template.py --name nmdc-agent-dev

Rebuild after changing dependencies (pyproject.toml / uv.lock). Code-only changes
don't need a rebuild: e2b_demo.py can upload a fresh wheel over the baked-in package.
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

try:
    from e2b import Template, default_build_logger
except ImportError:
    sys.exit("e2b is not installed. Run: uv sync --extra e2b")

REPO = Path(__file__).resolve().parent.parent
APP = "/opt/nmdc"

# Never ship secrets or local state into the template, even if a copy() is widened later.
IGNORE = [
    ".env",
    ".env.*",
    "**/settings.local.json",
    "**/*credentials*.json",
    "service-account.json",
    "**/*.pem",
    "**/*.key",
    "**/__pycache__",
    "**/*.pyc",
]

template = (
    Template(file_context_path=REPO, file_ignore_patterns=IGNORE)
    .from_python_image("3.12")
    .run_cmd("pip install --quiet uv", user="root")
    .set_envs(
        {
            # Keep the ENVO cache at a fixed path regardless of which user runs.
            "PYSTOW_HOME": f"{APP}/data",
            "UV_PROJECT_ENVIRONMENT": f"{APP}/.venv",
            "PATH": f"{APP}/.venv/bin:/usr/local/bin:/usr/bin:/bin",
        }
    )
    # Dependencies first, in their own layer, so code changes don't reinstall them.
    .copy(["pyproject.toml", "uv.lock", "README.md", "LICENSE"], f"{APP}/", user="root")
    .run_cmd(f"cd {APP} && uv sync --frozen --no-dev --no-install-project", user="root")
    .copy("src", f"{APP}/src", user="root")
    .copy(".claude/skills", f"{APP}/.claude/skills", user="root")
    .run_cmd(f"cd {APP} && uv sync --frozen --no-dev", user="root")
    # Fail the build early if the bundled CLI is missing or can't run on this image.
    .run_cmd(
        f"{APP}/.venv/bin/python -c "
        '"import claude_agent_sdk, pathlib, subprocess; '
        "cli = pathlib.Path(claude_agent_sdk.__file__).parent / '_bundled' / 'claude'; "
        "print(subprocess.run([str(cli), '--version'], check=True, "
        'capture_output=True, text=True).stdout)"',
        user="root",
    )
    .run_cmd(
        f"{APP}/.venv/bin/python -c "
        "\"from oaklib import get_adapter; get_adapter('sqlite:obo:envo'); "
        "print('ENVO cache warm')\"",
        user="root",
    )
    # Code and venv: readable by the sandbox user, writable only by root (used for the
    # fresh-wheel install). The pystow/ENVO cache must stay writable: pystow writes to
    # its home on import, and oaklib may write lock/index files next to the database.
    .run_cmd(f"chmod -R a+rX {APP} && chmod -R a+rwX {APP}/data", user="root")
    .set_workdir(APP)
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--name", default="nmdc-agent", help="template name")
    parser.add_argument("--cpu", type=int, default=2)
    parser.add_argument("--memory-mb", type=int, default=4096)
    parser.add_argument("--skip-cache", action="store_true", help="rebuild every layer")
    args = parser.parse_args()

    api_key = os.environ.get("E2B_API_KEY") or dotenv_values(REPO / ".env").get("E2B_API_KEY")
    if not api_key:
        sys.exit("E2B_API_KEY is not set (add it to .env or export it).")

    info = Template.build(
        template,
        args.name,
        cpu_count=args.cpu,
        memory_mb=args.memory_mb,
        skip_cache=args.skip_cache,
        on_build_logs=default_build_logger(),
        api_key=api_key,
    )
    print(f"built template {args.name!r}: {info}")


if __name__ == "__main__":
    main()
