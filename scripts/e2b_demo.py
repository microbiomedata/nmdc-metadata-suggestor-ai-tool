"""E2B demo 1: run the metadata mapper's apply phase inside a throwaway E2B sandbox.

No LLM and no model credentials are involved. Each run:
  1. builds a wheel of this package into a temp dir
  2. creates a fresh E2B sandbox
  3. uploads the wheel, the public phage CSV fixture, and a small worker script
  4. runs the worker (apply_mappings with a hard-coded mapping, including custom and
     combine expressions, so LLM-style generated code executes inside the sandbox)
  5. streams sandbox output to this terminal, prefixed with the sandbox id
  6. kills the sandbox in a finally block (Ctrl+C included)

Only E2B_API_KEY is read from .env (or the shell). Nothing else from .env is sent.

Run it in two terminals at once to see two independent sandboxes:

    uv sync --extra e2b
    uv run --extra e2b python scripts/e2b_demo.py
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dotenv import dotenv_values

try:
    from e2b import CommandExitException, Sandbox
except ImportError:
    sys.exit("e2b is not installed. Run: uv sync --extra e2b")

REPO = Path(__file__).resolve().parent.parent
CSV = REPO / "tests" / "fixtures" / "PRJEB13831_Phage_metadata.csv"
OUT_DIR = REPO / "workspace" / "e2b-demo"
REMOTE = "/home/user/demo"
TEMPLATE_VENV = "/opt/nmdc/.venv"  # where scripts/e2b_template.py installs the package

WORKER = r"""
import csv, json, platform, socket, sys, time
t0 = time.time()
from nmdc_metadata_suggestor_ai_tool.metadata_mapper import apply_mappings
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import MetadataMapperOutput

print(f"host={socket.gethostname()} python={platform.python_version()}", flush=True)

mapping = MetadataMapperOutput.model_validate({
    "source_files": [{"file_id": "phage", "display_name": "PRJEB13831_Phage_metadata.csv"}],
    "high_confidence": [
        {"source_column": "specimen_collector_sample_id", "source_file_id": "phage",
         "mixs_extension": "Water", "nmdc_candidate_slots": ["samp_name"],
         "confidence": "high", "reason": "sample id"},
        {"source_column": "sample_collection_start_date", "source_file_id": "phage",
         "mixs_extension": "Water", "nmdc_candidate_slots": ["collection_date"],
         "confidence": "high", "reason": "collection date",
         "conversion": {"type": "date_format", "description": "M/D/YYYY -> ISO",
                        "expression": "%m/%d/%Y"}},
        {"source_column": "organism", "source_file_id": "phage",
         "mixs_extension": "Water", "nmdc_candidate_slots": ["samp_taxon_id"],
         "confidence": "high", "reason": "organism",
         "conversion": {"type": "custom", "description": "normalize case",
                        "expression": "value.strip().lower()"}},
        {"source_column": "geo_loc_name_country",
         "combine_columns": ["geo_loc_name_state_province_territory"],
         "source_file_id": "phage", "mixs_extension": "Water",
         "nmdc_candidate_slots": ["geo_loc_name"], "confidence": "high",
         "reason": "country + region",
         "conversion": {"type": "custom", "description": "country: region",
                        "expression": "values['geo_loc_name_country'] + ': ' + "
                                      "values['geo_loc_name_state_province_territory']"}},
    ],
})

rows = list(csv.DictReader(open("phage.csv", newline="", encoding="utf-8")))[: int(sys.argv[1])]
print(f"applying 4 mappings to {len(rows)} rows (import took {time.time() - t0:.1f}s)", flush=True)
out = apply_mappings(mapping, rows)
keep = ["samp_name", "collection_date", "samp_taxon_id", "geo_loc_name", "_transform_errors"]
result = [{k: r[k] for k in keep if k in r} for r in out]
print("RESULT " + json.dumps(result), flush=True)
"""


def log(sid: str, msg: str) -> None:
    for line in msg.rstrip("\n").splitlines():
        print(f"[{sid[:8]}] {line}", flush=True)


def build_wheel(dest: Path) -> Path:
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dest)],
        cwd=REPO,
        check=True,
        capture_output=True,
    )
    return next(dest.glob("*.whl"))


def env_value(name: str) -> str | None:
    """Read one variable from the shell or .env. Never loads .env into the environment."""
    return os.environ.get(name) or dotenv_values(REPO / ".env").get(name)


def e2b_api_key() -> str:
    api_key = env_value("E2B_API_KEY")
    if not api_key:
        sys.exit("E2B_API_KEY is not set (add it to .env or export it).")
    return api_key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--rows", type=int, default=5, help="CSV rows to transform")
    parser.add_argument("--timeout", type=int, default=600, help="sandbox lifetime (s)")
    parser.add_argument(
        "--template",
        help="E2B template with deps preinstalled (e.g. nmdc-agent from e2b_template.py); "
        "only the fresh wheel is installed over it, without dependencies",
    )
    args = parser.parse_args()

    api_key = e2b_api_key()

    start = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        wheel = build_wheel(Path(tmp))
        print(f"built {wheel.name} in {time.time() - start:.1f}s", flush=True)

        sandbox = Sandbox.create(
            template=args.template,
            timeout=args.timeout,
            api_key=api_key,
            metadata={"purpose": "nmdc-e2b-demo"},
        )
        sid = sandbox.sandbox_id
        log(sid, f"sandbox {sid} created in {time.time() - start:.1f}s")
        result: list[dict] | None = None
        try:
            sandbox.files.write(f"{REMOTE}/{wheel.name}", wheel.read_bytes())
            sandbox.files.write(f"{REMOTE}/phage.csv", CSV.read_bytes())
            sandbox.files.write(f"{REMOTE}/worker.py", WORKER)

            t = time.time()
            if args.template:
                # Deps are baked in; overlay only this checkout's code. Root because the
                # template's venv is read-only to the sandbox user.
                log(sid, f"template {args.template}: installing fresh wheel (no deps) ...")
                sandbox.commands.run(
                    f"uv pip install --quiet --no-deps --reinstall "
                    f"--python {TEMPLATE_VENV} {wheel.name}",
                    cwd=REMOTE,
                    user="root",
                    timeout=120,
                    on_stderr=lambda s: log(sid, s),
                )
                python = f"{TEMPLATE_VENV}/bin/python"
            else:
                log(sid, "installing uv + package (Python 3.12) ...")
                sandbox.commands.run(
                    "pip install --quiet uv && "
                    f"uv venv --quiet --python 3.12 .venv && "
                    f"uv pip install --quiet --python .venv {wheel.name}",
                    cwd=REMOTE,
                    timeout=args.timeout,
                    on_stderr=lambda s: log(sid, s),
                )
                python = ".venv/bin/python"
            log(sid, f"install took {time.time() - t:.1f}s")

            def on_stdout(line: str) -> None:
                nonlocal result
                if line.startswith("RESULT "):
                    result = json.loads(line[len("RESULT ") :])
                else:
                    log(sid, line)

            sandbox.commands.run(
                f"{python} worker.py {args.rows}",
                cwd=REMOTE,
                timeout=120,
                on_stdout=on_stdout,
                on_stderr=lambda s: log(sid, s),
            )
        except CommandExitException as exc:
            log(sid, f"command failed (exit {exc.exit_code}): {exc.stderr[-2000:]}")
        finally:
            sandbox.kill()
            log(sid, f"sandbox killed; total {time.time() - start:.1f}s")

    if result is not None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_file = OUT_DIR / f"{sid}.json"
        out_file.write_text(json.dumps(result, indent=2))
        for row in result:
            log(sid, json.dumps(row))
        print(f"saved {out_file.relative_to(REPO)}")


if __name__ == "__main__":
    main()
