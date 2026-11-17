"""Build the `nmdc-agent` E2B template used by the E2B demos.

The template is built from a prebuilt Docker image (docker/e2b-agent-base.Dockerfile)
that bakes in everything slow or large, so a run only uploads its inputs (and
optionally a fresh wheel of the package):
  - Python 3.12 + uv
  - all locked runtime dependencies, in /opt/nmdc/.venv
  - the package itself, plus .claude/skills/ (the agent reads skills from its cwd)
  - the Claude Code CLI, which claude-agent-sdk ships as a bundled binary
  - a warmed ENVO cache, so env-triad lookups don't download the ontology per run

Dependencies are installed by Docker, not in E2B build steps: self-hosted E2B runs
build steps on a ~800M rootfs, too small for the venv plus the bundled CLI.

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--image", required=True, help="base image built from the Dockerfile")
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

    api_key = os.environ.get("E2B_API_KEY") or dotenv_values(REPO / ".env").get("E2B_API_KEY")
    if not api_key:
        sys.exit("E2B_API_KEY is not set (add it to .env or export it).")

    info = Template.build(
        build_template(args.image, args.gcp_key),
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
