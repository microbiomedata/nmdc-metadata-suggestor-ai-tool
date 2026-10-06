"""Light eval: does handing the mapper agent a saved linkml-map transform help? (issue 177)

The question is whether a transform learned from one submission can be reused on a later
submission with the same shape, and what that buys: fewer tokens and less time, and the same
or steadier mappings.

Setup. One CSV is split by rows into two files with identical headers: ``earlier`` stands in
for a past submission and ``later`` for a new one in the same shape. A transform is learned by
running the agent on ``earlier`` and compiling its output (treated as approved; no human
review), or loaded from ``--transform``.

Arms, all on ``later``:

| arm              | agent? | what it gets                                              |
|------------------|--------|-----------------------------------------------------------|
| cold             | yes    | the columns and sample values, as today                   |
| with_transform   | yes    | the same, plus the saved transform's mappings in the prompt|
| transform_only   | no     | the saved transform, run through linkml-map               |

There is no gold mapping, so agreement is measured against the first cold run. With
``--reps`` of 2 or more, cold runs are also compared with each other: that is the noise floor,
and ``with_transform`` agreement only means something relative to it.

Usage:
    uv run python -m nmdc_metadata_suggestor_ai_tool.evaluation.transform_reuse
    uv run python -m nmdc_metadata_suggestor_ai_tool.evaluation.transform_reuse --reps 3
    uv run python -m nmdc_metadata_suggestor_ai_tool.evaluation.transform_reuse \\
        --transform evaluation-results/transform-reuse/<run>/learned_transform

Results go to ``evaluation-results/transform-reuse/<timestamp>/``: the split files, the learned
transform, every arm's mapper output and transformed rows, ``results.yaml`` with all metrics,
and ``report.md``.
"""

import argparse
import asyncio
import csv
import itertools
import json
import logging
import statistics
import time
from pathlib import Path
from typing import Any

import yaml

from nmdc_metadata_suggestor_ai_tool.evaluation.mapper_comparison import (
    HEALTH_FIELDS,
    compare_mappings,
    compare_values,
    confidence_counts,
    count_transform_errors,
    mapped_slots,
    run_cost,
)
from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient
from nmdc_metadata_suggestor_ai_tool.metadata_mapper import (
    TransformLibrary,
    apply_mappings,
    compile_transform,
    mapper_output_from_transform,
    match_transform,
    rebase_transform,
    run_metadata_mapper_agentic,
    run_transform,
)
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.transform_spec import read_transform_files
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    MetadataMapperOutput,
    SourceFile,
)
from nmdc_metadata_suggestor_ai_tool.models.reusable_transform import ReusableTransform

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CSV = REPO_ROOT / "tests" / "fixtures" / "PRJEB13831_Phage_metadata.csv"
DEFAULT_OUT = REPO_ROOT / "evaluation-results" / "transform-reuse"
DEFAULT_EXTENSIONS = ["water"]

logger = logging.getLogger("transform_reuse_eval")


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def perturb_header(header: str) -> str:
    """Same column, different spelling: underscores to spaces, title case."""
    return header.replace("_", " ").title()


def split_csv(csv_path: Path, out_dir: Path, perturb_headers: bool) -> tuple[Path, Path, list[str]]:
    """Split rows in half into ``earlier.csv`` and ``later.csv``. Returns later's headers."""
    headers, rows = read_rows(csv_path)
    half = len(rows) // 2
    earlier, later = out_dir / "earlier.csv", out_dir / "later.csv"
    write_rows(earlier, headers, rows[:half])
    later_headers = [perturb_header(h) for h in headers] if perturb_headers else headers
    renamed = [dict(zip(later_headers, (r[h] for h in headers), strict=True)) for r in rows[half:]]
    write_rows(later, later_headers, renamed)
    return earlier, later, later_headers


def run_agent(
    client: LLMClient,
    path: Path,
    file_id: str,
    extensions: list[str],
    transform: ReusableTransform | None = None,
) -> tuple[MetadataMapperOutput, float]:
    headers, _ = read_rows(path)
    match = match_transform(headers, [transform], min_overlap=0.0) if transform else None
    source = SourceFile(file_id=file_id, display_name=path.name)
    start = time.monotonic()
    output, _ = asyncio.run(
        run_metadata_mapper_agentic(
            llm_client=client,
            csv_files=[(source, path)],
            mixs_extensions=extensions,
            prior_transform=match,
        )
    )
    return output, time.monotonic() - start


def dump(path: Path, data: Any) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def score_arm(
    name: str,
    output: MetadataMapperOutput,
    rows: list[dict[str, Any]],
    reference: MetadataMapperOutput,
    reference_rows: list[dict[str, Any]],
    wall_s: float | None,
) -> dict[str, Any]:
    slots = mapped_slots(output) | mapped_slots(reference)
    return {
        "arm": name,
        "wall_seconds": round(wall_s, 1) if wall_s is not None else None,
        "cost": run_cost(output),
        "confidence": confidence_counts(output),
        "transform_errors": count_transform_errors(rows),
        "vs_reference_mappings": compare_mappings(reference, output),
        "vs_reference_values": compare_values(reference_rows, rows, slots),
    }


def mean_of(records: list[dict[str, Any]], *path: str) -> float | None:
    values = []
    for record in records:
        value: Any = record
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, int | float):
            values.append(value)
    return round(statistics.mean(values), 4) if values else None


def summarize(scores: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for arm in dict.fromkeys(s["arm"] for s in scores):
        records = [s for s in scores if s["arm"] == arm]
        summary[arm] = {
            "runs": len(records),
            "wall_seconds": mean_of(records, "wall_seconds"),
            **{field: mean_of(records, "cost", field) for field in HEALTH_FIELDS},
            "slot_agreement": mean_of(records, "vs_reference_mappings", "slot_agreement"),
            "conversion_agreement": mean_of(
                records, "vs_reference_mappings", "conversion_agreement"
            ),
            "value_agreement": mean_of(records, "vs_reference_values", "value_agreement"),
            "transform_errors": mean_of(records, "transform_errors"),
        }
    return summary


def pairwise_agreement(outputs: list[MetadataMapperOutput]) -> dict[str, float | None]:
    """Mean slot and conversion agreement over every pair of runs from the same arm."""
    pairs = [compare_mappings(a, b) for a, b in itertools.combinations(outputs, 2)]
    return {
        "pairs": len(pairs),
        "slot_agreement": mean_of(pairs, "slot_agreement"),
        "conversion_agreement": mean_of(pairs, "conversion_agreement"),
    }


def write_report(path: Path, results: dict[str, Any]) -> None:
    summary = results["summary"]
    columns = [
        ("runs", "runs"),
        ("wall_seconds", "wall s"),
        ("num_turns", "turns"),
        ("input_tokens", "input tok"),
        ("output_tokens", "output tok"),
        ("cache_read_tokens", "cache read tok"),
        ("total_cost_usd", "cost $"),
        ("slot_agreement", "slot agree"),
        ("conversion_agreement", "conv agree"),
        ("value_agreement", "value agree"),
        ("transform_errors", "cell errors"),
    ]
    lines = [
        "# Transform reuse eval (issue 177)",
        "",
        f"- Source CSV: `{results['setup']['csv']}`",
        f"- Provider / model: {results['setup']['provider']} / {results['setup']['model']}",
        f"- Reps: {results['setup']['reps']}; headers perturbed: "
        f"{results['setup']['perturb_headers']}",
        f"- Transform: `{results['setup']['transform']}` "
        f"({results['transform']['mappings']} mappings, "
        f"{results['transform']['overlap_with_later']:.0%} column overlap with `later`)",
        "",
        "Agreement is against cold run 1 on `later`, not against a gold mapping. Cold rep 1 "
        "agrees with itself by definition, so its row is excluded from the cold agreement "
        "means below; read `with_transform` against the cold noise floor.",
        "",
        "| arm | " + " | ".join(label for _, label in columns) + " |",
        "|---|" + "---|" * len(columns),
    ]
    for arm, row in summary.items():
        cells = ["" if row.get(key) is None else str(row[key]) for key, _ in columns]
        lines.append(f"| {arm} | " + " | ".join(cells) + " |")
    lines += ["", "## Run-to-run consistency (same arm, every pair of reps)", ""]
    for arm, stats in results["consistency"].items():
        lines.append(
            f"- **{arm}**: {stats['pairs']} pairs, slot agreement {stats['slot_agreement']}, "
            f"conversion agreement {stats['conversion_agreement']}"
        )
    lines += [
        "",
        "## Transform-only coverage",
        "",
        f"- Columns covered by the saved transform: {results['transform_only']['covered']}",
        f"- Columns left for the agent: {results['transform_only']['uncovered']}",
        f"- linkml-map run matches `apply_mappings` on the rebuilt output: "
        f"{results['transform_only']['parity_with_apply_mappings']}",
    ]
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--extensions", nargs="+", default=DEFAULT_EXTENSIONS)
    parser.add_argument("--provider", default="gcp", choices=["gcp", "cborg", "pnnl"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--reps", type=int, default=1, help="agent runs per arm")
    parser.add_argument(
        "--transform", type=Path, default=None, help="saved transform folder; skips learning"
    )
    parser.add_argument(
        "--perturb-headers",
        action="store_true",
        help="respell later.csv's headers (case, spaces) to exercise shape matching",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    out = args.out_dir / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    earlier, later, later_headers = split_csv(args.csv, out, args.perturb_headers)
    _, later_rows = read_rows(later)
    client = LLMClient(access_provider=args.provider, model=args.model)

    # Learn the transform from the earlier file, or load a saved one.
    if args.transform:
        transform = read_transform_files(args.transform)
    else:
        logger.info("learning transform from %s", earlier.name)
        earlier_headers, _ = read_rows(earlier)
        learned, _ = run_agent(client, earlier, "earlier", args.extensions)
        dump(out / "earlier_mapper_output.yaml", learned.model_dump(mode="json"))
        transform = compile_transform(
            learned,
            earlier_headers,
            name="learned_transform",
            mixs_extensions=args.extensions,
            description=f"agent output on the first half of {args.csv.name}, unreviewed",
        )
    TransformLibrary(out).save(transform.model_copy(update={"name": "learned_transform"}))
    match = match_transform(later_headers, [transform], min_overlap=0.0)
    assert match is not None

    # Agent arms on the later file.
    outputs: dict[str, list[MetadataMapperOutput]] = {"cold": [], "with_transform": []}
    walls: dict[str, list[float]] = {"cold": [], "with_transform": []}
    for rep in range(1, args.reps + 1):
        for arm, prior in (("cold", None), ("with_transform", transform)):
            logger.info("rep %d: %s", rep, arm)
            output, wall = run_agent(client, later, "later", args.extensions, prior)
            outputs[arm].append(output)
            walls[arm].append(wall)
            dump(out / f"{arm}_rep{rep}_mapper_output.yaml", output.model_dump(mode="json"))

    reference = outputs["cold"][0]
    reference_rows = apply_mappings(reference, later_rows)
    scores = []
    for arm in ("cold", "with_transform"):
        for rep, (output, wall) in enumerate(zip(outputs[arm], walls[arm], strict=True), 1):
            if arm == "cold" and rep == 1:
                continue  # the reference itself
            rows = apply_mappings(output, later_rows)
            dump(out / f"{arm}_rep{rep}_rows.yaml", rows)
            score = score_arm(arm, output, rows, reference, reference_rows, wall)
            score["rep"] = rep
            scores.append(score)

    # No-LLM arm: rebuild mapper output from the transform and run it through linkml-map.
    later_source = SourceFile(file_id="later", display_name=later.name)
    rebuilt, uncovered = mapper_output_from_transform(transform, later_source, later_headers)
    rebased = rebase_transform(transform, later_headers)
    spec_rows = run_transform(rebased, later_rows)
    dump(out / "transform_only_rows.yaml", spec_rows)
    scores.append(score_arm("transform_only", rebuilt, spec_rows, reference, reference_rows, None))
    slots = mapped_slots(rebuilt)
    apply_rows = apply_mappings(rebuilt, later_rows)
    parity = all(
        {k: v for k, v in a.items() if k in slots} == {k: v for k, v in b.items() if k in slots}
        for a, b in zip(apply_rows, spec_rows, strict=True)
    )

    results = {
        "setup": {
            "csv": str(args.csv),
            "extensions": args.extensions,
            "provider": args.provider,
            "model": reference.model,
            "reps": args.reps,
            "perturb_headers": args.perturb_headers,
            "transform": str(args.transform or out / "learned_transform"),
            "rows": {"earlier": len(read_rows(earlier)[1]), "later": len(later_rows)},
        },
        "transform": {
            "mappings": len(transform.mappings),
            "overlap_with_later": round(match.overlap, 4),
            "missing_columns": match.missing_columns,
            "new_columns": match.new_columns,
        },
        "reference_cost": run_cost(reference),
        "summary": summarize(
            [
                {"arm": "cold", "wall_seconds": walls["cold"][0], "cost": run_cost(reference)},
                *scores,
            ]
        ),
        "consistency": {arm: pairwise_agreement(outs) for arm, outs in outputs.items()},
        "transform_only": {
            "covered": len(later_headers) - len(uncovered),
            "uncovered": len(uncovered),
            "parity_with_apply_mappings": parity,
        },
        "scores": scores,
    }
    dump(out / "results.yaml", json.loads(json.dumps(results, default=str)))
    write_report(out / "report.md", results)
    print((out / "report.md").read_text())
    print(f"Results: {out}")


if __name__ == "__main__":
    main()
