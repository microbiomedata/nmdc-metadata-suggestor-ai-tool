# Base image for the `nmdc-agent` E2B template (see scripts/e2b_template.py).
#
# Dependencies are installed here, by Docker, instead of inside an E2B build sandbox:
# self-hosted E2B starts build steps on a ~800M rootfs, too small for the venv plus
# the Claude CLI that claude-agent-sdk bundles. E2B sizes an imported image's rootfs
# to fit the image.
#
# Build and push from the repo root (linux/amd64 to match the E2B hosts):
#   docker build --platform linux/amd64 -f docker/e2b-agent-base.Dockerfile \
#     -t REGION-docker.pkg.dev/PROJECT/REPO/nmdc-agent-base:TAG .
#   docker push REGION-docker.pkg.dev/PROJECT/REPO/nmdc-agent-base:TAG
#
# No credentials go into this image: .dockerignore excludes .env and key files.

FROM python:3.12-slim

ENV APP=/opt/nmdc
ENV PYSTOW_HOME=/opt/nmdc/data \
    UV_PROJECT_ENVIRONMENT=/opt/nmdc/.venv \
    PATH=/opt/nmdc/.venv/bin:/usr/local/bin:/usr/bin:/bin

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates openssl \
    && rm -rf /var/lib/apt/lists/* && pip install --quiet --no-cache-dir uv

WORKDIR /opt/nmdc

# Dependencies first, in their own layer, so code changes don't reinstall them.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project && rm -rf /root/.cache/uv

COPY src ./src
COPY .claude/skills ./.claude/skills
RUN uv sync --frozen --no-dev && rm -rf /root/.cache/uv

# Fail the build early if the bundled CLI is missing or can't run on this image.
RUN python -c "import claude_agent_sdk, pathlib, subprocess; \
cli = pathlib.Path(claude_agent_sdk.__file__).parent / '_bundled' / 'claude'; \
print(subprocess.run([str(cli), '--version'], check=True, capture_output=True, text=True).stdout)"

# Warm the ENVO cache so env-triad lookups don't download the ontology per run.
RUN python -c "from oaklib import get_adapter; get_adapter('sqlite:obo:envo'); print('ENVO cache warm')" \
    && find /opt/nmdc/data -name '*.gz' -delete

# Code and venv: readable by the sandbox user, writable only by root (used for the
# fresh-wheel install). The pystow/ENVO cache must stay writable.
RUN chmod -R a+rX /opt/nmdc && chmod -R a+rwX /opt/nmdc/data
