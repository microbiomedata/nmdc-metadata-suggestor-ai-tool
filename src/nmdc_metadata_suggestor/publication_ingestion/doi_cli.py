"""CLI for DOI validation and classification.

Usage::

    make validate-doi DOI=10.1038/s41564-020-00861-0
    make classify-doi DOI=10.1038/s41564-020-00861-0
    make classify-fixture
"""

import json
import sys

from nmdc_metadata_suggestor.publication_ingestion.doi_utils import (
    classify_doi,
    normalize_doi,
    validate_doi,
)


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
    from pathlib import Path

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


def main() -> None:
    """Entry point."""
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <validate|classify|classify-fixture> [DOI]", file=sys.stderr)
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
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(f"Usage: {sys.argv[0]} <validate|classify|classify-fixture> [DOI]", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
