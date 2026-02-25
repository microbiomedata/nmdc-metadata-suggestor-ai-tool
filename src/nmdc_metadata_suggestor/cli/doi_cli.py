"""CLI for DOI validation, classification, and abstract retrieval.

Usage::

    make validate-doi DOI=10.1038/s41564-020-00861-0
    make classify-doi DOI=10.1038/s41564-020-00861-0
    make classify-fixture
    make get-abstract DOI=10.1038/s41564-020-00861-0
    make get-abstract DOI=10.1038/s41564-020-00861-0 OUT=abstracts/
    make get-abstracts
"""

import json
import sys
from pathlib import Path

from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    classify_doi,
    normalize_doi,
    validate_doi,
)
from nmdc_metadata_suggestor.publication_ingestion.abstract_retriever import get_abstract


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
    result = get_abstract(doi, sources=sources)
    dump = result.model_dump()

    if result.error:
        print(f"  {result.error}", file=sys.stderr)
    else:
        source = result.source or "unknown"
        length = len(result.content) if result.content else 0
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

        result = get_abstract(doi)
        dump = result.model_dump()

        slug = doi.replace("/", "__")
        file_path = out_path / f"{slug}.json"
        file_path.write_text(json.dumps(dump, indent=2) + "\n")

        if result.content:
            source = result.source or "unknown"
            length = len(result.content)
            print(f"  {source:25s}  {length:>6d} chars  {doi}", file=sys.stderr)
        else:
            print(f"  NO ABSTRACT             {doi}", file=sys.stderr)

    print(f"\nResults saved to {out_path}/", file=sys.stderr)


def main() -> None:
    """Entry point."""
    if len(sys.argv) < 2:
        print(
            f"Usage: {sys.argv[0]} <validate|classify|classify-fixture"
            "|get-abstract|get-abstracts> [DOI] [OUT]",
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
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(
            f"Usage: {sys.argv[0]} <validate|classify|classify-fixture"
            "|get-abstract|get-abstracts> [DOI] [OUT]",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
