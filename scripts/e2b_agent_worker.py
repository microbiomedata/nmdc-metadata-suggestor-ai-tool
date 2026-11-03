"""Runs INSIDE an E2B sandbox started by e2b_agent_demo.py. Not meant to run locally.

Calls one of the real agentic pipelines and writes the result to result.json.
The agent's text and tool calls are printed as they happen so the local terminal
can watch the run.

    python e2b_agent_worker.py preflight
    python e2b_agent_worker.py probe-langfuse
    python e2b_agent_worker.py mapper    <csv-path>  <out-json>
    python e2b_agent_worker.py env-triad <json-path> <out-json>
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# E2B runs commands through a login shell, which resets PATH, so a PATH passed with
# the command doesn't survive. Put this interpreter's venv first here instead: the
# Claude CLI inherits it, and so does every Bash tool call it makes, so the agent's
# `python3` and `uv run python` resolve to the venv that has the package.
os.environ["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"

# Debian's system trust store, where the template image's CA certificates live.
SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"

PREFLIGHT = (
    "command -v python3 && python3 -c "
    "'from nmdc_metadata_suggestor_ai_tool.envo import get_envo; get_envo(); "
    'print("preflight ok: package + ENVO cache")\''
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ResultMessage,
    ToolResultBlock,
    UserMessage,
)

from nmdc_metadata_suggestor_ai_tool import llm_client, tracing  # noqa: E402
from nmdc_metadata_suggestor_ai_tool.metadata_mapper import pipeline as mapper  # noqa: E402
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (  # noqa: E402
    SourceFile,
)
from nmdc_metadata_suggestor_ai_tool.system_prompt import env_triad_prompt  # noqa: E402

HEARTBEAT_S = 30
_last_event = time.time()
_started = time.time()
# What the agent actually returned, before the pipeline parses and validates it.
# Saved next to the parsed result so a run that comes back empty can be diagnosed.
raw: dict[str, Any] = {"structured_output_calls": [], "result_message": None}


def say(line: str) -> None:
    global _last_event
    _last_event = time.time()
    print(f"{time.time() - _started:6.0f}s {line}", flush=True)


def show(content: Any) -> None:
    """Print each agent turn so the local terminal can follow the run."""
    for block in content if isinstance(content, list) else [content]:
        if isinstance(block, str):
            say(f"agent: {block[:400]}")
        elif getattr(block, "text", None):
            say(f"agent: {block.text[:400]}")
        elif hasattr(block, "name") and hasattr(block, "input"):
            say(f"tool  {block.name}: {json.dumps(block.input)[:400]}")


def _result_text(block: ToolResultBlock) -> str:
    content = block.content
    if isinstance(content, list):
        content = " ".join(str(part.get("text", part)) for part in content)
    return " ".join(str(content or "").split())


def watch(query: Any) -> Any:
    """Wrap the SDK's query() to also print tool results, which the pipelines don't
    log. Every event is passed through unchanged."""

    async def watched(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        async for event in query(*args, **kwargs):
            if isinstance(event, AssistantMessage):
                for block in event.content or []:
                    if getattr(block, "name", None) == "StructuredOutput":
                        raw["structured_output_calls"].append(block.input)
            elif isinstance(event, ResultMessage):
                raw["result_message"] = {
                    "structured_output": event.structured_output,
                    "permission_denials": event.permission_denials,
                    "num_turns": event.num_turns,
                    "total_cost_usd": event.total_cost_usd,
                    "is_error": event.is_error,
                }
            if isinstance(event, UserMessage) and isinstance(event.content, list):
                for block in event.content:
                    if isinstance(block, ToolResultBlock):
                        status = "ERROR" if block.is_error else "ok"
                        say(f"  -> {status}: {_result_text(block)[:300]}")
            yield event

    return watched


def heartbeat() -> None:
    """A model turn can take minutes with no output; say so instead of looking hung."""
    while True:
        time.sleep(HEARTBEAT_S)
        idle = time.time() - _last_event
        if idle >= HEARTBEAT_S:
            print(
                f"{time.time() - _started:6.0f}s ... waiting on the model ({idle:.0f}s quiet)",
                flush=True,
            )


def show_and_trace(content: Any) -> None:
    show(content)
    tracing.log_assistant_message(content)  # the Langfuse event; no-op when tracing is off


llm_client.log_assistant_message = show_and_trace
mapper.log_assistant_message = show_and_trace
llm_client.query = watch(llm_client.query)
mapper.query = watch(mapper.query)


def gcp_client() -> llm_client.LLMClient:
    """The agentic paths only read access_provider/model, so skip LLMClient.__init__:
    it would build a Gemini client and refresh a token the agent never uses."""
    client = llm_client.LLMClient.__new__(llm_client.LLMClient)
    client.access_provider = "gcp"
    client.model = llm_client.DEFAULT_CLAUDE_MODEL
    return client


async def run(pipeline: str, input_path: Path) -> dict[str, Any]:
    if pipeline == "mapper":
        source = SourceFile(file_id="phage", display_name=input_path.name)
        result, session_id = await mapper.run_metadata_mapper_agentic(
            llm_client=gcp_client(),
            csv_files=[(source, input_path)],
            mixs_extensions=["water"],
        )
        return {"session_id": session_id, "output": result.model_dump(mode="json")}

    biosample = json.loads(input_path.read_text())
    samples = biosample.get("biosample_set", [biosample])[:1]
    cm = llm_client.ConversationManager(llm_client=gcp_client(), system_prompt=env_triad_prompt)
    result, session_id = await cm.agentic(
        message=str({"samples": samples, "interface_names": ["soil"]}),
        interface_names=["SoilInterface"],
    )
    output = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    return {"session_id": session_id, "output": output}


def probe_langfuse() -> int:
    """Check the sandbox can reach Langfuse through E2B's egress proxy, before spending.

    Header injection means the proxy terminates TLS with its own certificate. Clients
    that use certifi's bundle (requests, so the OTel exporter) reject it unless they
    are pointed at a bundle that trusts the proxy. Report who issued the certificate
    we see and whether each bundle accepts it.
    """
    import socket
    import ssl
    from urllib.parse import urlparse

    import certifi
    import requests

    base = os.environ["LANGFUSE_BASE_URL"].rstrip("/")
    host = urlparse(base).hostname or ""
    unverified = ssl.create_default_context()
    unverified.check_hostname, unverified.verify_mode = False, ssl.CERT_NONE
    try:
        with socket.create_connection((host, 443), timeout=10) as sock:
            with unverified.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
        issuer = subprocess.run(
            ["openssl", "x509", "-noout", "-issuer"],
            input=ssl.DER_cert_to_PEM_cert(der),
            capture_output=True,
            text=True,
        ).stdout.strip()
        print(f"langfuse probe: {host} cert {issuer}", flush=True)
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        print(f"langfuse probe: could not read {host} cert: {exc!r}", flush=True)

    found = subprocess.run(
        "grep -rlis e2b /usr/local/share/ca-certificates /etc/ssl/certs 2>/dev/null | head",
        shell=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    print(f"langfuse probe: CA files mentioning e2b: {found or 'none'}", flush=True)

    ok = False
    for label, bundle in (("certifi", certifi.where()), ("system", SYSTEM_CA_BUNDLE)):
        try:
            r = requests.get(f"{base}/api/public/health", timeout=15, verify=bundle)
            print(f"langfuse probe: {label} bundle -> HTTP {r.status_code}", flush=True)
            ok = ok or (label == "system" and r.ok)
        except Exception as exc:  # noqa: BLE001 - diagnostics only
            print(f"langfuse probe: {label} bundle -> {type(exc).__name__}: {exc}", flush=True)
    return 0 if ok else 1


def main() -> None:
    if sys.argv[1] == "probe-langfuse":
        sys.exit(probe_langfuse())
    if sys.argv[1] == "preflight":
        # Same chain the agent's Bash tool uses: this process's env -> a shell -> python3.
        # Fails in seconds, before any model spend.
        sys.exit(subprocess.run(["bash", "-c", PREFLIGHT]).returncode)
    pipeline, input_path, out_path = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    t0 = time.time()
    threading.Thread(target=heartbeat, daemon=True).start()
    payload: dict[str, Any] = {"session_id": None, "output": None}
    if tracing.langfuse_client is not None:
        say(f"Langfuse tracing on ({os.environ.get('LANGFUSE_BASE_URL')})")
    try:
        # No-op when tracing is off. Tags every span so E2B runs can be filtered.
        with tracing.propagate_attributes(
            tags=["e2b", pipeline],
            metadata={"sandbox_id": os.environ.get("NMDC_E2B_SANDBOX_ID", "")},
        ):
            payload.update(asyncio.run(run(pipeline, input_path)))
    except Exception as exc:
        # Keep what the agent produced even when the pipeline fails afterwards.
        payload["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        payload["seconds"] = round(time.time() - t0, 1)
        payload["raw"] = raw
        out_path.write_text(json.dumps(payload, indent=2, default=str))
        # Spans are exported in batches; the sandbox is killed right after this exits.
        tracing.flush()
        denials = (raw["result_message"] or {}).get("permission_denials") or []
        for denial in denials:
            tool_input = denial.get("tool_input", {}) if isinstance(denial, dict) else {}
            say(f"denied {denial.get('tool_name', '?')}: {json.dumps(tool_input)[:300]}")
        print(
            f"done in {payload['seconds']}s, session {payload['session_id']}, "
            f"{len(raw['structured_output_calls'])} StructuredOutput call(s) saved",
            flush=True,
        )


if __name__ == "__main__":
    main()
