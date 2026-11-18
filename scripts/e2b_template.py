"""Build the `nmdc-agent` E2B template used by the E2B demos.

The template is built from a prebuilt Docker image (docker/e2b-agent-base.Dockerfile)
that bakes in everything slow or large, so a run only uploads its inputs (and
optionally a fresh wheel of the package):
  - Python 3.12 + uv
  - all locked runtime dependencies, in /opt/nmdc/.venv
  - the package itself, plus .claude/skills/ (the agent reads skills from its cwd)
  - the Claude Code CLI, which claude-agent-sdk ships as a bundled binary
  - a warmed ENVO cache, so env-triad lookups don't download the ontology per run

Dependencies are installed by Docker, not in E2B build steps: Our current self-hosted E2B deployment runs out of build filesystem
space while installing dependencies. min_free_disk_mb requests free
space after the build, not installation-time workspace. Preinstalling
dependencies in the Docker image avoids that failing installation step.

No credentials go into the template. Every sandbox started from it can read all of
it. The GCP service-account key passed here is only used by E2B to pull the image.

Usage:
    # 1. build + push the base image (rebuild after changing deps or code)
    docker build --platform linux/amd64 -f docker/e2b-agent-base.Dockerfile -t IMAGE .
    docker push IMAGE
    # 2. build the E2B template from it
    uv run --extra e2b python scripts/e2b_template.py --image IMAGE --gcp-key key.json
    uv run --extra e2b python scripts/e2b_template.py --image IMAGE --name nmdc-agent-dev

IMAGE is an Artifact Registry path, e.g.
REGION-docker.pkg.dev/PROJECT/REPO/nmdc-agent-base:TAG. --gcp-key defaults to
$E2B_GCP_KEY_FILE; omit both for a public image. Code-only changes can skip both
steps: e2b_demo.py can upload a fresh wheel over the baked-in package.
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


def build_template(image: str, gcp_key: str | None):
    base = Template()
    builder = (
        base.from_gcp_registry(image, service_account_json=gcp_key)
        if gcp_key
        else base.from_image(image)
    )
    # Image ENV may not carry over into sandboxes, so set it again on the template.
    return builder.set_envs(
        {
            "PYSTOW_HOME": f"{APP}/data",
            "UV_PROJECT_ENVIRONMENT": f"{APP}/.venv",
            "PATH": f"{APP}/.venv/bin:/usr/local/bin:/usr/bin:/bin",
        }
    ).set_workdir(APP)


def build_template_no_docker():
    """Experimental: build the same environment with E2B build steps, no Docker image.

    Self-hosted E2B runs these steps on a small (~800M) rootfs. To keep peak disk use
    low, uv's cache and temp dir live on the same filesystem as the venv, so uv can
    hardlink packages instead of copying them (in the build sandbox /tmp is a
    different filesystem, which forces a full second copy). The cache is deleted in
    the same step. If this still runs out of space, fall back to the Docker workflow.
    """
    sync = (
        f"cd {APP} && "
        f"mkdir -p {APP}/.uv-cache && "
        "df -hT / /tmp . && df -i / /tmp . && "
        f"TMPDIR={APP}/.uv-cache "
        f"UV_CACHE_DIR={APP}/.uv-cache "
        "UV_LINK_MODE=hardlink "
        "uv sync --frozen --no-dev {extra}; "
        "status=$?; "
        "df -hT / /tmp .; "
        "df -i / /tmp .; "
        f"du -sh {APP}/.venv {APP}/.uv-cache 2>/dev/null; "
        f'if [ "$status" -eq 0 ]; then rm -rf {APP}/.uv-cache; fi; '
        'exit "$status"'
    )
    return (
        Template(file_context_path=str(REPO))
        .from_image("python:3.12-slim")
        .set_envs(
            {
                "PYSTOW_HOME": f"{APP}/data",
                "UV_PROJECT_ENVIRONMENT": f"{APP}/.venv",
                "PATH": f"{APP}/.venv/bin:/usr/local/bin:/usr/bin:/bin",
            }
        )
        .run_cmd("df -h /")
        .run_cmd(
            "apt-get update && apt-get install -y --no-install-recommends ca-certificates openssl"
            " && rm -rf /var/lib/apt/lists/* && pip install --quiet --no-cache-dir uv"
            f" && mkdir -p {APP}",
            user="root",
        )
        # Dependencies first, so code changes don't reinstall them.
        .copy(["pyproject.toml", "uv.lock", "README.md", "LICENSE"], f"{APP}/", user="root")
        .run_cmd(sync.format(extra="--no-install-project"), user="root")
        .copy("src", f"{APP}/src", user="root")
        .copy(".claude/skills", f"{APP}/.claude/skills", user="root")
        .run_cmd(sync.format(extra=""), user="root")
        # Fail early if the bundled Claude CLI is missing or can't run.
        .run_cmd(
            f'{APP}/.venv/bin/python -c "import claude_agent_sdk, pathlib, subprocess; '
            "cli = pathlib.Path(claude_agent_sdk.__file__).parent / '_bundled' / 'claude'; "
            "print(subprocess.run([str(cli), '--version'], check=True, capture_output=True, "
            'text=True).stdout)"',
            user="root",
        )
        # Warm the ENVO cache so env-triad lookups don't download the ontology per run.
        .run_cmd(
            f"{APP}/.venv/bin/python -c "
            "\"from oaklib import get_adapter; get_adapter('sqlite:obo:envo')\" "
            f"&& find {APP}/data -name '*.gz' -delete",
            user="root",
        )
        .run_cmd(f"chmod -R a+rX {APP} && chmod -R a+rwX {APP}/data", user="root")
        .set_workdir(APP)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--image", help="base image built from the Dockerfile")
    parser.add_argument(
        "--no-docker",
        action="store_true",
        help="experimental: install deps with E2B build steps instead of a prebuilt image",
    )
    parser.add_argument(
        "--gcp-key",
        default=os.environ.get("E2B_GCP_KEY_FILE"),
        help="service-account JSON with Artifact Registry read access (default $E2B_GCP_KEY_FILE)",
    )
    parser.add_argument("--name", default="nmdc-agent", help="template name")
    parser.add_argument("--cpu", type=int, default=2)
    parser.add_argument("--memory-mb", type=int, default=4096)
    parser.add_argument(
        "--min-free-disk-mb",
        type=int,
        default=2048,
        help="minimum free disk space after build (MiB)",
    )
    parser.add_argument("--skip-cache", action="store_true", help="rebuild every layer")
    args = parser.parse_args()
    if not args.no_docker and not args.image:
        parser.error("--image is required unless --no-docker is set")

    api_key = os.environ.get("E2B_API_KEY") or dotenv_values(REPO / ".env").get("E2B_API_KEY")
    if not api_key:
        sys.exit("E2B_API_KEY is not set (add it to .env or export it).")

    info = Template.build(
        build_template_no_docker() if args.no_docker else build_template(args.image, args.gcp_key),
        args.name,
        cpu_count=args.cpu,
        memory_mb=args.memory_mb,
        min_free_disk_mb=args.min_free_disk_mb,
        skip_cache=args.skip_cache,
        on_build_logs=default_build_logger(),
        api_key=api_key,
    )
    print(f"built template {args.name!r}: {info}")


if __name__ == "__main__":
    main()
