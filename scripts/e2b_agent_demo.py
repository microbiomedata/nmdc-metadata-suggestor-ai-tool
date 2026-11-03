"""E2B demo 2: run a real agentic pipeline (Claude Agent SDK on Vertex) in an E2B sandbox.

Each run starts its own sandbox from the `nmdc-agent` template (build it first with
scripts/e2b_template.py), installs a fresh wheel of this checkout over it, runs
scripts/e2b_agent_worker.py, streams the agent's turns here, saves the result, and
kills the sandbox (Ctrl+C included).

Credentials: the GCP service-account key you pass with --gcp-credentials is copied
into this run's sandbox only, never into the template. The agent process can read it,
so use a dedicated key with just the Vertex AI User role. To limit what a misbehaving
agent could do with it, the sandbox can only reach *.googleapis.com.

From .env this reads only E2B_API_KEY, VERTEX_PROJECT_ID and GCP_REGION, plus, with
--langfuse, LANGFUSE_PUBLIC_KEY, LANGFUSE_BASE_URL and LANGFUSE_TRACING_ENVIRONMENT.

--langfuse traces the run to Langfuse from inside the sandbox. The secret key never
enters the sandbox: run scripts/e2b_langfuse_secret.py once to store it in E2B, and
E2B's egress proxy sets the real Authorization header on requests to the Langfuse host.
The sandbox only gets a placeholder secret key.

    uv run --extra e2b python scripts/e2b_agent_demo.py mapper \\
        --gcp-credentials ~/keys/nmdc-e2b-trial.json
    uv run --extra e2b python scripts/e2b_agent_demo.py env-triad --langfuse \\
        --gcp-credentials ~/keys/nmdc-e2b-trial.json
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from e2b_demo import OUT_DIR, REPO, TEMPLATE_VENV, build_wheel, e2b_api_key, env_value, log
from e2b_langfuse_secret import SECRET_NAME as LANGFUSE_SECRET

try:
    from e2b import CommandExitException, Sandbox, Secret
except ImportError:
    sys.exit("e2b is not installed. Run: uv sync --extra e2b")

APP = "/opt/nmdc"  # template's project dir: the agent runs here so it finds .claude/skills
REMOTE = "/home/user/run"
INPUTS = {
    "mapper": REPO / "tests" / "fixtures" / "PRJEB13831_Phage_metadata.csv",
    "env-triad": REPO / "tests" / "fixtures" / "biosample.json",
}
# Vertex (regional endpoint + OAuth token exchange) and nothing else.
ALLOWED_HOSTS = ["*.googleapis.com"]
# Not a credential: the egress proxy replaces the Authorization header it ends up in.
# The SDK just needs a non-empty value to turn tracing on.
LANGFUSE_PLACEHOLDER_SECRET = "sk-lf-set-by-e2b-egress-proxy"
SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"


def langfuse_setup(agent_env: dict[str, str], network: dict) -> str:
    """Point the sandbox at Langfuse and have the egress proxy add the real auth."""
    public_key = env_value("LANGFUSE_PUBLIC_KEY")
    base_url = env_value("LANGFUSE_BASE_URL") or "https://us.cloud.langfuse.com"
    if not public_key:
        sys.exit("--langfuse needs LANGFUSE_PUBLIC_KEY in .env or the shell.")
    host = urlparse(base_url).hostname
    if not host or urlparse(base_url).scheme != "https":
        # The proxy only injects secrets into HTTPS requests.
        sys.exit(f"LANGFUSE_BASE_URL must be an https URL, got {base_url!r}.")
    agent_env.update(
        {
            "LANGFUSE_PUBLIC_KEY": public_key,
            "LANGFUSE_SECRET_KEY": LANGFUSE_PLACEHOLDER_SECRET,
            "LANGFUSE_BASE_URL": base_url,
            "LANGFUSE_TRACING_ENVIRONMENT": env_value("LANGFUSE_TRACING_ENVIRONMENT") or "local",
            # Injecting the header means E2B's proxy terminates TLS with its own cert.
            # requests (and so the OTel exporter) trusts only certifi's bundle by default
            # and rejects it; point both at the system store, which trusts the proxy.
            "REQUESTS_CA_BUNDLE": SYSTEM_CA_BUNDLE,
            "SSL_CERT_FILE": SYSTEM_CA_BUNDLE,
            "OTEL_EXPORTER_OTLP_CERTIFICATE": SYSTEM_CA_BUNDLE,
        }
    )
    auth = f"Basic {Secret.fill(LANGFUSE_SECRET)}"
    network["allow_out"] = [*network["allow_out"], host]
    network["rules"] = {host: [{"transform": {"headers": {"Authorization": auth}}}]}
    return host


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("pipeline", choices=sorted(INPUTS))
    parser.add_argument(
        "--gcp-credentials",
        required=True,
        type=Path,
        help="service-account JSON to send to this sandbox (use a dedicated, scoped key)",
    )
    parser.add_argument("--template", default="nmdc-agent")
    parser.add_argument("--timeout", type=int, default=900, help="sandbox lifetime (s)")
    parser.add_argument(
        "--langfuse",
        action="store_true",
        help="trace to Langfuse (run scripts/e2b_langfuse_secret.py once first)",
    )
    args = parser.parse_args()

    api_key = e2b_api_key()
    key_bytes = args.gcp_credentials.expanduser().read_bytes()
    project = env_value("VERTEX_PROJECT_ID") or json.loads(key_bytes).get("project_id")
    region = env_value("GCP_REGION") or "us-east5"
    if not project:
        sys.exit("No Vertex project: set VERTEX_PROJECT_ID or use a key with project_id.")

    agent_env = {
        "CLAUDE_CODE_USE_VERTEX": "1",
        "ANTHROPIC_VERTEX_PROJECT_ID": project,
        "CLOUD_ML_REGION": region,
        "GOOGLE_APPLICATION_CREDENTIALS": f"{REMOTE}/gcp_credentials.json",
        # Keep Claude Code from calling anything but Vertex (telemetry, updates, ...);
        # the egress allowlist would block those anyway, this just avoids the noise.
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        # The venv is read-only; skills call `uv run python`, which must not try to sync.
        "UV_NO_SYNC": "1",
        "UV_FROZEN": "1",
        # The template's set_envs() does not reach sandbox commands, so repeat what the
        # run depends on. Without PYSTOW_HOME, oaklib misses the baked-in ENVO cache and
        # tries to download it, which the egress allowlist blocks. PATH is not set here:
        # the login shell resets it, so the worker puts its venv first itself.
        "PYSTOW_HOME": f"{APP}/data",
        "UV_PROJECT_ENVIRONMENT": TEMPLATE_VENV,
    }
    network: dict = {"allow_out": ALLOWED_HOSTS, "deny_out": lambda ctx: [ctx.all_traffic]}
    langfuse_host = None
    if args.langfuse:
        if not Secret.exists(LANGFUSE_SECRET, api_key=api_key):
            sys.exit(f"E2B secret {LANGFUSE_SECRET!r} not found: run e2b_langfuse_secret.py.")
        langfuse_host = langfuse_setup(agent_env, network)

    start = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        wheel = build_wheel(Path(tmp))
        print(f"built {wheel.name} in {time.time() - start:.1f}s", flush=True)

        sandbox = Sandbox.create(
            template=args.template,
            timeout=args.timeout,
            api_key=api_key,
            metadata={"purpose": "nmdc-e2b-agent-demo", "pipeline": args.pipeline},
            network=network,
        )
        sid = sandbox.sandbox_id
        agent_env["NMDC_E2B_SANDBOX_ID"] = sid  # tagged on the Langfuse trace
        log(sid, f"sandbox {sid} created from {args.template} in {time.time() - start:.1f}s")
        log(sid, f"pipeline={args.pipeline} project={project} region={region}")
        if langfuse_host:
            log(sid, f"tracing to {langfuse_host} (auth added by the E2B egress proxy)")
        result = None
        try:
            input_file = INPUTS[args.pipeline]
            sandbox.files.write(f"{REMOTE}/{wheel.name}", wheel.read_bytes())
            sandbox.files.write(f"{REMOTE}/{input_file.name}", input_file.read_bytes())
            sandbox.files.write(
                f"{REMOTE}/worker.py", (REPO / "scripts" / "e2b_agent_worker.py").read_text()
            )
            sandbox.files.write(f"{REMOTE}/gcp_credentials.json", key_bytes)
            sandbox.commands.run(f"chmod 600 {REMOTE}/gcp_credentials.json")

            sandbox.commands.run(
                f"uv pip install --quiet --no-deps --reinstall "
                f"--python {TEMPLATE_VENV} {REMOTE}/{wheel.name}",
                user="root",
                timeout=120,
                on_stderr=lambda s: log(sid, s),
            )
            if langfuse_host:
                # Fail before any model spend if traces can't reach Langfuse via the proxy.
                sandbox.commands.run(
                    f"{TEMPLATE_VENV}/bin/python {REMOTE}/worker.py probe-langfuse",
                    envs=agent_env,
                    timeout=60,
                    on_stdout=lambda s: log(sid, s),
                    on_stderr=lambda s: log(sid, s),
                )
            # Preflight with the agent's env: fail in seconds, before any model spend, if
            # the ENVO cache or the package can't be loaded the way the agent will load it.
            sandbox.commands.run(
                f"{TEMPLATE_VENV}/bin/python {REMOTE}/worker.py preflight",
                cwd=APP,
                envs=agent_env,
                timeout=120,
                on_stdout=lambda s: log(sid, s),
                on_stderr=lambda s: log(sid, s),
            )
            log(sid, f"ready in {time.time() - start:.1f}s; starting agent ...")

            sandbox.commands.run(
                f"{TEMPLATE_VENV}/bin/python {REMOTE}/worker.py {args.pipeline} "
                f"{REMOTE}/{input_file.name} {REMOTE}/result.json",
                cwd=APP,
                envs=agent_env,
                timeout=args.timeout,
                on_stdout=lambda s: log(sid, s),
                on_stderr=lambda s: log(sid, s),
            )
        except CommandExitException as exc:
            log(sid, f"command failed (exit {exc.exit_code})")
        except KeyboardInterrupt:
            log(sid, "interrupted")
        finally:
            # The worker writes result.json even when the pipeline fails, so fetch it
            # either way: it holds the agent's raw answer, which dies with the sandbox.
            try:
                result = json.loads(sandbox.files.read(f"{REMOTE}/result.json"))
            except Exception:  # noqa: BLE001 - no result is a normal outcome here
                log(sid, "no result.json to fetch")
            sandbox.kill()
            log(sid, f"sandbox killed (key gone with it); total {time.time() - start:.1f}s")

    if result is not None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_file = OUT_DIR / f"{args.pipeline}-{sid}.json"
        out_file.write_text(json.dumps(result, indent=2))
        print(f"saved {out_file.relative_to(REPO)}")


if __name__ == "__main__":
    main()
