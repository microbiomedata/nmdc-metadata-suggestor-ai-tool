"""Issue 143: does supplement retrieval change env triad suggestions?

Runs the env triad recommender over the phyllosphere study
(``nmdc:sty-11-e4yb9z58``, publication DOI 10.1038/s41467-023-36515-y) in two
arms and scores both against two references:

* **publication** -- the study name, description, publication and award DOIs
  (abstract fetched by the pipeline), and the biosample records with their
  triad removed.
* **publication+supplements** -- the same, plus every text-like supplement the
  retriever keeps for the publication DOI (the two per-sample metadata CSVs).

References: the triad the authors wrote into supplement MOESM4 (keyed by
sample name) and the triad currently stored in NMDC for those biosamples.

Usage::

    uv run python scripts/eval_phyllosphere_supplements.py --out eval_output/phyllosphere
    uv run python scripts/eval_phyllosphere_supplements.py --limit 8   # cheap smoke run

Needs LLM credentials (GCP by default, as the integration tests use).
"""

import argparse
import csv
import io
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from nmdc_metadata_suggestor_ai_tool.constants import ENV_TRIAD_SLOTS  # noqa: E402
from nmdc_metadata_suggestor_ai_tool.env_triad_recommendation import (  # noqa: E402
    get_env_triad_recommendation,
)
from nmdc_metadata_suggestor_ai_tool.evaluation.env_triad_scoring import (  # noqa: E402
    Reference,
    compare_outputs,
    reference_from_biosamples,
    reference_from_rows,
    rekey_reference,
    score_output,
)
from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient  # noqa: E402
from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput  # noqa: E402
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import (  # noqa: E402
    remove_temp_files,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements import (  # noqa: E402
    format_supplement_context,
    retrieve_supplements,
)

FIXTURES = REPO / "tests" / "fixtures"
STUDY_ID = "nmdc:sty-11-e4yb9z58"
PUBLICATION_DOI = "10.1038/s41467-023-36515-y"
AWARD_DOI = "10.46936/10.25585/60000818"
INTERFACE = "plant-associated"
SUPPLEMENT_REFERENCE_FILE = "41467_2023_36515_MOESM4_ESM.csv"

logger = logging.getLogger("eval_phyllosphere")


class ErrorCollector(logging.Handler):
    """Keep the pipeline's per-chunk error messages; it logs them and returns nothing."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def load_json(name: str) -> Any:
    with (FIXTURES / name).open() as f:
        return json.load(f)


def submission_object_for(study: dict) -> dict:
    """A submission-portal shaped object carrying only what the study record gives.

    Going through the submission path (rather than ``study_context``) makes the
    pipeline fetch the publication abstract itself, exactly as it would for a
    real submission, and exercises the award DOI routing the ticket asks about.
    """
    return {
        "metadata_submission": {
            "packageName": [INTERFACE],
            "studyForm": {
                "studyName": study["name"],
                "description": study["description"],
                "publicationDois": [{"value": PUBLICATION_DOI, "provider": None}],
            },
            "multiOmicsForm": {"awardDois": [{"value": AWARD_DOI, "provider": "jgi"}]},
        }
    }


def strip_triad(samples: list[dict]) -> list[dict]:
    """Copies of *samples* with the env triad removed, so the model must supply it."""
    stripped = []
    for sample in samples:
        copy = dict(sample)
        for slot in ENV_TRIAD_SLOTS:
            copy.pop(slot, None)
        stripped.append(copy)
    return stripped


def supplement_reference(result_files: list, samples: list[dict]) -> tuple[Reference, list[str]]:
    """Build the supplement reference keyed by NMDC id; return it with unmatched sample ids."""
    table = next((f for f in result_files if f.filename == SUPPLEMENT_REFERENCE_FILE), None)
    if table is None or not table.text:
        raise SystemExit(f"{SUPPLEMENT_REFERENCE_FILE} was not among the retrieved supplements")
    rows = list(csv.DictReader(io.StringIO(table.text)))
    by_name = reference_from_rows(rows, id_column="sample_name")
    name_to_id = {s.get("name") or s.get("samp_name"): s["id"] for s in samples}
    reference = rekey_reference(by_name, name_to_id)
    unmatched = [s["id"] for s in samples if s["id"] not in reference]
    return reference, unmatched


def run_arm(
    name: str,
    llm_client: LLMClient,
    submission_object: dict,
    samples: list[dict],
    study_context: list[str],
    chunk_size: int,
) -> tuple[LLMOutput, float, list[str]]:
    """Run one arm; return its output, wall time, and any per-chunk pipeline errors."""
    logger.info(f"arm {name}: {len(samples)} samples, {len(study_context)} context messages")
    collector = ErrorCollector()
    pipeline_logger = logging.getLogger("nmdc_metadata_suggestor_ai_tool.env_triad_recommendation")
    pipeline_logger.addHandler(collector)
    started = time.time()
    try:
        output = get_env_triad_recommendation(
            llm_client=llm_client,
            samples=samples,
            submission_object=submission_object,
            study_context=study_context,
            interface_names=[INTERFACE],
            chunk_size=chunk_size,
        )
    finally:
        pipeline_logger.removeHandler(collector)
    elapsed = time.time() - started
    logger.info(f"arm {name}: {len(output.metadata_fields)} suggestions in {elapsed:.0f}s")
    return output, elapsed, collector.messages


def coverage(output: LLMOutput, sample_ids: list[str]) -> dict:
    """How many samples got a triad back, and how many suggestions carried no usable id.

    The model sometimes drops the id field for a whole chunk, or folds a chunk
    into one unlabeled triad; those suggestions cannot be scored and are counted
    here so a low accuracy is not mistaken for a wrong answer.
    """
    known = set(sample_ids)
    labeled = {f.id for f in output.metadata_fields if f.id in known}
    unlabeled = sum(1 for f in output.metadata_fields if not f.id)
    foreign = sum(1 for f in output.metadata_fields if f.id and f.id not in known)
    return {
        "n_samples": len(sample_ids),
        "samples_with_suggestions": len(labeled),
        "suggestions_total": len(output.metadata_fields),
        "suggestions_without_id": unlabeled,
        "suggestions_with_unknown_id": foreign,
    }


def percent(numerator: int, denominator: int) -> str:
    return f"{100 * numerator / denominator:.0f}%" if denominator else "n/a"


def render_report(results: dict) -> str:
    """Markdown summary of the run: accuracy per arm and reference, and the arm delta."""
    lines = [
        "# Phyllosphere supplement evaluation (issue 143)",
        "",
        f"Study `{STUDY_ID}`, publication DOI `{PUBLICATION_DOI}`, "
        f"model `{results['model']}` via `{results['access_provider']}`, "
        f"{results['n_samples']} biosamples, chunk size {results['chunk_size']}.",
        "",
        "## Retrieval",
        "",
        f"- Supplements kept: {results['supplements']['n_kept']} "
        f"(inlined as text: {results['supplements']['n_inlined']}), "
        f"skipped: {results['supplements']['n_skipped']}, "
        f"retrieval time {results['supplements']['elapsed_s']:.1f}s",
        f"- Supplement context: {results['supplements']['context_chars']:,} characters",
        f"- Samples without a supplement row: {len(results['supplements']['unmatched'])}",
        "",
        "## Coverage",
        "",
        "| Arm | samples answered | suggestions | without id | unknown id | chunk errors |",
        "|---|---|---|---|---|---|",
    ]
    for arm in ("publication", "publication+supplements"):
        cov = results["coverage"][arm]
        lines.append(
            f"| {arm} | {cov['samples_with_suggestions']}/{cov['n_samples']} | "
            f"{cov['suggestions_total']} | {cov['suggestions_without_id']} | "
            f"{cov['suggestions_with_unknown_id']} | {len(results['chunk_errors'][arm])} |"
        )
    for arm in ("publication", "publication+supplements"):
        for message in results["chunk_errors"][arm]:
            lines.append(f"- {arm}: `{message[:300]}`")
    lines += [
        "",
        "Accuracy below is over samples that have a reference value, so an unanswered",
        "sample counts as a miss. Compare against the coverage row before reading it.",
        "",
        "## CURIE accuracy per slot",
        "",
        "| Slot | Reference | publication | publication+supplements |",
        "|---|---|---|---|",
    ]
    for slot in ENV_TRIAD_SLOTS:
        for ref_name in ("supplement", "nmdc"):
            cells = []
            for arm in ("publication", "publication+supplements"):
                score = results["scores"][arm][ref_name][slot]
                cells.append(
                    f"{score['curie_matches']}/{score['n_with_reference']} "
                    f"({percent(score['curie_matches'], score['n_with_reference'])})"
                )
            lines.append(f"| {slot} | {ref_name} | {cells[0]} | {cells[1]} |")

    lines += ["", "## What each arm suggested", ""]
    for arm in ("publication", "publication+supplements"):
        lines.append(f"### {arm} ({results['timings_s'][arm]:.0f}s)")
        lines.append("")
        for slot in ENV_TRIAD_SLOTS:
            score = results["scores"][arm]["supplement"][slot]
            top = ", ".join(f"`{v}` ×{n}" for v, n in list(score["values"].items())[:5])
            tiers = ", ".join(f"{k} {v}" for k, v in score["tiers"].items()) or "no provenance"
            outcomes = ", ".join(f"{k} {v}" for k, v in score["outcomes"].items())
            lines.append(f"- **{slot}** ({score['n_suggested']} suggested): {top}")
            lines.append(f"  - gate: {tiers}; {outcomes}")
        lines.append("")

    lines += ["## Change from adding supplements", ""]
    lines.append("| Slot | changed | toward supplement | away from supplement | toward NMDC | away from NMDC |")
    lines.append("|---|---|---|---|---|---|")
    for slot in ENV_TRIAD_SLOTS:
        vs_supp = results["deltas"]["supplement"][slot]
        vs_nmdc = results["deltas"]["nmdc"][slot]
        lines.append(
            f"| {slot} | {vs_supp['changed']}/{vs_supp['n_compared']} | "
            f"{vs_supp['toward_reference']} | {vs_supp['away_from_reference']} | "
            f"{vs_nmdc['toward_reference']} | {vs_nmdc['away_from_reference']} |"
        )
    lines.append("")
    for slot in ENV_TRIAD_SLOTS:
        transitions = results["deltas"]["supplement"][slot]["transitions"]
        if transitions:
            lines.append(f"Transitions for {slot}:")
            lines.append("")
            for transition, n in transitions.items():
                lines.append(f"- {n}× `{transition}`")
            lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", default="gcp", choices=["gcp", "cborg", "pnnl"])
    parser.add_argument("--model", default=None, help="model name; provider default when omitted")
    # The pipeline's own default is 50. At 50, gemini-2.5-flash dropped the id
    # field for a whole chunk in one arm and truncated its JSON in the other
    # (see docs/eval-phyllosphere-supplements.md), so the evaluation defaults lower.
    parser.add_argument("--chunk-size", type=int, default=25)
    parser.add_argument("--limit", type=int, default=None, help="only the first N biosamples")
    parser.add_argument("--out", type=Path, default=REPO / "eval_output" / "phyllosphere")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    credentials = REPO / "gcp_credentials.json"
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and credentials.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials)

    study = load_json("phyllosphere_study.json")
    samples = load_json("phyllosphere_biosamples.json")["resources"]
    if args.limit:
        samples = samples[: args.limit]
    sample_ids = [s["id"] for s in samples]
    nmdc_reference = reference_from_biosamples(samples)
    stripped = strip_triad(samples)
    submission_object = submission_object_for(study)

    started = time.time()
    retrieved = retrieve_supplements(PUBLICATION_DOI)
    retrieval_elapsed = time.time() - started
    supplement_messages = format_supplement_context(retrieved)
    supp_reference, unmatched = supplement_reference(retrieved.files, samples)
    if unmatched:
        logger.warning(f"{len(unmatched)} samples have no row in {SUPPLEMENT_REFERENCE_FILE}")
    remove_temp_files([f.saved_path for f in retrieved.files if f.saved_path])

    llm_client = LLMClient(access_provider=args.provider, model=args.model)
    arms: dict[str, list[str]] = {
        "publication": [],
        "publication+supplements": supplement_messages,
    }
    outputs: dict[str, LLMOutput] = {}
    timings: dict[str, float] = {}
    chunk_errors: dict[str, list[str]] = {}
    for name, context in arms.items():
        outputs[name], timings[name], chunk_errors[name] = run_arm(
            name, llm_client, submission_object, stripped, context, args.chunk_size
        )

    references = {"supplement": supp_reference, "nmdc": nmdc_reference}
    results = {
        "study_id": STUDY_ID,
        "publication_doi": PUBLICATION_DOI,
        "model": llm_client.model,
        "access_provider": llm_client.access_provider,
        "n_samples": len(samples),
        "chunk_size": args.chunk_size,
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "supplements": {
            "source": retrieved.source,
            "pmcid": retrieved.pmcid,
            "n_kept": len(retrieved.files),
            "n_inlined": sum(1 for f in retrieved.files if f.text),
            "n_skipped": len(retrieved.skipped),
            "kept": [f.filename for f in retrieved.files],
            "elapsed_s": retrieval_elapsed,
            "context_chars": sum(len(m) for m in supplement_messages),
            "unmatched": unmatched,
        },
        "timings_s": timings,
        "chunk_errors": chunk_errors,
        "coverage": {arm: coverage(output, sample_ids) for arm, output in outputs.items()},
        "scores": {
            arm: {
                ref_name: {slot: score.as_dict() for slot, score in score_output(output, reference, sample_ids).items()}
                for ref_name, reference in references.items()
            }
            for arm, output in outputs.items()
        },
        "deltas": {
            ref_name: {
                slot: delta.as_dict()
                for slot, delta in compare_outputs(
                    outputs["publication"], outputs["publication+supplements"], reference, sample_ids
                ).items()
            }
            for ref_name, reference in references.items()
        },
        "outputs": {arm: output.model_dump() for arm, output in outputs.items()},
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(results, indent=2))
    report = render_report(results)
    (args.out / "report.md").write_text(report)
    print(report)
    print(f"\nWrote {args.out / 'results.json'} and {args.out / 'report.md'}")


if __name__ == "__main__":
    main()
