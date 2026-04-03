"""CLI for DOI validation, classification, and abstract retrieval.

Usage::

    make validate-doi DOI=10.1038/s41564-020-00861-0
    make classify-doi DOI=10.1038/s41564-020-00861-0
    make classify-fixture
    make get-abstract DOI=10.1038/s41564-020-00861-0
    make get-abstract DOI=10.1038/s41564-020-00861-0 OUT=abstracts/
    make get-abstracts
    make get-abstracts-from-file FILE=dois.tsv
"""

import csv
import json
import sys
from pathlib import Path

from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    classify_doi,
    normalize_doi,
    validate_doi,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.main import get_doi_description_or_abstract


def _print_json(obj: object) -> None:
    print(json.dumps(obj, indent=2))


def cmd_validate(raw_doi: str) -> None:
    """Validate a single DOI via the Handle API."""
    doi = normalize_doi(raw_doi)
    result = validate_doi(doi)
    _print_json(result.model_dump())


def cmd_classify(raw_doi: str) -> None:
    """Classify a single DOI by registration agency, type, and NMDC category."""
    doi = normalize_doi(raw_doi)
    result = classify_doi(doi)
    _print_json(result.model_dump())


def cmd_classify_fixture() -> None:
    """Classify all valid DOIs in the test fixture."""
    fixture_path = Path(__file__).parents[3] / "tests" / "fixtures" / "doi_test_cases.json"
    with open(fixture_path) as f:
        data = json.load(f)

    results = []
    for tc in data["test_cases"]:
        doi = tc["doi"]
        if not doi:
            continue
        result = classify_doi(doi)
        results.append(result.model_dump())
        status = "OK" if result.is_valid else "INVALID"
        category = result.inferred_nmdc_category or result.error or "unknown"
        print(f"  {status:7s}  {category:30s}  {doi}", file=sys.stderr)

    _print_json(results)


def cmd_get_abstract(
    raw_doi: str, out_dir: str | None = None, sources: list[str] | None = None
) -> None:
    """Fetch the abstract for a single DOI and print or save it.

    If *out_dir* is provided, writes the result JSON to a file in that
    directory named ``{doi_slug}.json``. Otherwise prints to stdout.

    If *sources* is provided, only those sources are tried (in the given
    order). Otherwise the default waterfall is used.
    """
    doi = normalize_doi(raw_doi)
    result = get_doi_description_or_abstract(doi, sources=sources)
    dump = result.model_dump()

    if result.error:
        print(f"  {result.error}", file=sys.stderr)
    else:
        source = result.source or "unknown"
        length = len(result.context) if result.context else 0
        print(f"  {source:25s}  {length:>6d} chars  {doi}", file=sys.stderr)

    if out_dir:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        slug = doi.replace("/", "__")
        file_path = out_path / f"{slug}.json"
        file_path.write_text(json.dumps(dump, indent=2) + "\n")
        print(f"  -> {file_path}", file=sys.stderr)
    else:
        _print_json(dump)


def cmd_get_abstracts(out_dir: str = "abstracts") -> None:
    """Fetch abstracts for all publication DOIs in the test fixture.

    Skips DOIs that are invalid or not publications. Saves results as
    individual JSON files in *out_dir*.
    """
    fixture_path = Path(__file__).parents[3] / "tests" / "fixtures" / "doi_test_cases.json"
    with open(fixture_path) as f:
        data = json.load(f)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    for tc in data["test_cases"]:
        doi = tc["doi"]
        if not doi or not tc.get("valid"):
            continue
        if tc.get("expected_nmdc_category") != "publication_doi":
            print(f"  SKIP (not publication)  {doi}", file=sys.stderr)
            continue

        result = get_doi_description_or_abstract(doi)
        dump = result.model_dump()

        slug = doi.replace("/", "__")
        file_path = out_path / f"{slug}.json"
        file_path.write_text(json.dumps(dump, indent=2) + "\n")

        if result.context:
            source = result.source or "unknown"
            length = len(result.context)
            print(f"  {source:25s}  {length:>6d} chars  {doi}", file=sys.stderr)
        else:
            print(f"  NO ABSTRACT             {doi}", file=sys.stderr)

    print(f"\nResults saved to {out_path}/", file=sys.stderr)


def _read_dois_from_file(file_path: Path) -> list[str]:
    """Read DOIs from a TSV, CSV, or plain text file.

    Supports:
    - TSV/CSV with a ``doi`` or ``doi_value`` column header
    - Plain text with one DOI per line
    """
    text = file_path.read_text()
    first_line = text.split("\n", 1)[0]

    sep = "\t" if "\t" in first_line else ","
    if "doi" in first_line.lower():
        reader = csv.DictReader(text.splitlines(), delimiter=sep)
        fieldnames = [f.lower().strip() for f in (reader.fieldnames or [])]
        if "doi_value" in fieldnames:
            col = "doi_value"
        elif "doi" in fieldnames:
            col = "doi"
        else:
            print(f"Error: no 'doi' or 'doi_value' column in {file_path}", file=sys.stderr)
            sys.exit(1)
        actual_col = (reader.fieldnames or [])[fieldnames.index(col)]
        return list(
            dict.fromkeys(
                row[actual_col].strip() for row in reader if row.get(actual_col, "").strip()
            )
        )

    return list(dict.fromkeys(line.strip() for line in text.splitlines() if line.strip()))


def _print_coverage_summary(results: list[dict]) -> None:
    """Print a coverage summary table to stderr."""
    total = len(results)
    with_text = sum(1 for r in results if r.get("context"))
    without_text = total - with_text

    source_counts: dict[str, int] = {}
    context_type_counts: dict[str, int] = {}
    text_lengths: list[int] = []
    error_counts: dict[str, int] = {}

    for r in results:
        if r.get("context"):
            src = r.get("source") or "unknown"
            source_counts[src] = source_counts.get(src, 0) + 1
            ct = r.get("context_type") or "unknown"
            context_type_counts[ct] = context_type_counts.get(ct, 0) + 1
            text_lengths.append(len(r["context"]))
        if r.get("error"):
            err = r["error"][:60]
            error_counts[err] = error_counts.get(err, 0) + 1

    print("\n--- Coverage Summary ---", file=sys.stderr)
    print(f"Total DOIs:      {total}", file=sys.stderr)
    pct = f" ({100 * with_text // total}%)" if total else ""
    print(f"With text:       {with_text}{pct}", file=sys.stderr)
    print(f"Without text:    {without_text}", file=sys.stderr)

    if text_lengths:
        avg_len = sum(text_lengths) // len(text_lengths)
        print(
            f"Text length:     min={min(text_lengths)}, "
            f"avg={avg_len}, max={max(text_lengths)} chars",
            file=sys.stderr,
        )

    if source_counts:
        print("\nBy source:", file=sys.stderr)
        for src, count in sorted(source_counts.items(), key=lambda x: -x[1]):
            print(f"  {src:25s}  {count}", file=sys.stderr)

    if context_type_counts:
        print("\nBy context type:", file=sys.stderr)
        for ct, count in sorted(context_type_counts.items(), key=lambda x: -x[1]):
            print(f"  {ct:25s}  {count}", file=sys.stderr)

    if error_counts:
        print("\nErrors:", file=sys.stderr)
        for err, count in sorted(error_counts.items(), key=lambda x: -x[1]):
            print(f"  {count:3d}x  {err}", file=sys.stderr)


def cmd_get_abstracts_from_file(
    file_path: str,
    out_dir: str = "results",
    all_categories: bool = True,
    sources: list[str] | None = None,
) -> None:
    """Fetch text for DOIs listed in an external file.

    By default runs ALL DOIs through the waterfall (publications and
    datasets alike) by skipping the classification gate.  Set
    *all_categories* to ``False`` to restore the publication-only gate.
    Saves per-DOI JSON results and prints a coverage summary.
    """
    dois = _read_dois_from_file(Path(file_path))
    if not dois:
        print(f"No DOIs found in {file_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Processing {len(dois)} DOIs from {file_path}", file=sys.stderr)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    all_results: list[dict] = []

    for i, raw_doi in enumerate(dois, 1):
        doi = normalize_doi(raw_doi)
        result = get_doi_description_or_abstract(
            doi, skip_classification=all_categories, sources=sources
        )
        dump = result.model_dump()
        all_results.append(dump)

        slug = doi.replace("/", "__")
        file_out = out_path / f"{slug}.json"
        file_out.write_text(json.dumps(dump, indent=2) + "\n")

        if result.context:
            source = result.source or "unknown"
            length = len(result.context)
            print(
                f"  [{i}/{len(dois)}] {source:25s}  {length:>6d} chars  {doi}",
                file=sys.stderr,
            )
        else:
            err = (result.error or "unknown")[:40]
            print(f"  [{i}/{len(dois)}] NO TEXT  {err:40s}  {doi}", file=sys.stderr)

    summary_path = out_path / "_all_results.json"
    summary_path.write_text(json.dumps(all_results, indent=2) + "\n")

    _print_coverage_summary(all_results)
    print(f"\nResults saved to {out_path}/", file=sys.stderr)
    print(f"Combined results: {summary_path}", file=sys.stderr)


def main() -> None:
    """Entry point."""
    if len(sys.argv) < 2:
        print(
            f"Usage: {sys.argv[0]} <validate|classify|classify-fixture"
            "|get-abstract|get-abstracts|get-abstracts-from-file> [DOI] [OUT]",
            file=sys.stderr,
        )
        sys.exit(1)

    command = sys.argv[1]

    if command == "validate":
        if len(sys.argv) < 3:
            print("Error: DOI argument required", file=sys.stderr)
            sys.exit(1)
        cmd_validate(sys.argv[2])
    elif command == "classify":
        if len(sys.argv) < 3:
            print("Error: DOI argument required", file=sys.stderr)
            sys.exit(1)
        cmd_classify(sys.argv[2])
    elif command == "classify-fixture":
        cmd_classify_fixture()
    elif command == "get-abstract":
        if len(sys.argv) < 3:
            print("Error: DOI argument required", file=sys.stderr)
            sys.exit(1)
        out_dir = None
        sources = None
        for arg in sys.argv[3:]:
            if arg.startswith("sources="):
                sources = arg[len("sources=") :].split(",")
            else:
                out_dir = arg
        cmd_get_abstract(sys.argv[2], out_dir, sources)
    elif command == "get-abstracts":
        out_dir = sys.argv[2] if len(sys.argv) > 2 else "abstracts"
        cmd_get_abstracts(out_dir)
    elif command == "get-abstracts-from-file":
        if len(sys.argv) < 3:
            print("Error: FILE argument required", file=sys.stderr)
            sys.exit(1)
        file_arg = sys.argv[2]
        out_dir = sys.argv[3] if len(sys.argv) > 3 else "results"
        sources = None
        for arg in sys.argv[4:]:
            if arg.startswith("sources="):
                sources = arg[len("sources=") :].split(",")
        cmd_get_abstracts_from_file(file_arg, out_dir, sources=sources)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(
            f"Usage: {sys.argv[0]} <validate|classify|classify-fixture"
            "|get-abstract|get-abstracts|get-abstracts-from-file> [DOI] [OUT]",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
