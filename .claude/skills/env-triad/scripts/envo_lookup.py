#!/usr/bin/env python3
"""Constrained ENVO lookup and env triad validation for the env-triad skill.

Every operation the skill needs, as a fixed verb set. Use this rather than
writing Python: the subcommands take only valid slots and interface names, and
`validate` exits non-zero when a value would fail submission portal validation,
so a bad value cannot pass unnoticed.

    uv run python .claude/skills/env-triad/scripts/envo_lookup.py <command> ...

Commands:
    biomes                       Every ENVO biome -- the complete env_broad_scale set
    pool INTERFACE SLOT          Where ENVO expansion may draw candidates
    search TEXT                  Ranked candidates with definitions
    descendants CURIE            Follow the train down to more specific terms
    fallback INTERFACE SLOT      The tier-3 value for this extension and slot
    validate SLOT VALUE...       Gate values before emitting them

The heavy lifting lives in nmdc_metadata_suggestor_ai_tool.envo_index, which the
non-agentic pipeline shares; this is only a bounded entry point onto it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Importable standalone: fall back to the checkout's src/ when the package is not
# already installed in the active environment.
if __package__ is None:  # pragma: no cover - exercised only outside the venv
    _src = Path(__file__).resolve().parents[4] / "src"
    if (_src / "nmdc_metadata_suggestor_ai_tool").is_dir():
        sys.path.insert(0, str(_src))

from nmdc_metadata_suggestor_ai_tool.constants import ENV_TRIAD_SLOTS  # noqa: E402
from nmdc_metadata_suggestor_ai_tool.envo_index import get_envo_index  # noqa: E402
from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import (  # noqa: E402
    MixsExtensions,
)

INTERFACES = sorted(MixsExtensions.__members__)


def resolve_interface(name: str) -> str:
    """Accept a portal name ("soil") or a class name ("SoilInterface")."""
    resolved = MixsExtensions.map_to_interface_name([name])
    if not resolved:
        raise SystemExit(
            f"unknown MIxS extension {name!r}. Portal names or class names, one of:\n  "
            + "\n  ".join(f"{m.name} ({m.value})" for m in MixsExtensions)
        )
    return resolved[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="envo_lookup.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("biomes", help="every ENVO biome (the complete env_broad_scale set)")

    pool = sub.add_parser("pool", help="where ENVO expansion may draw candidates")
    pool.add_argument("interface")
    pool.add_argument("slot", choices=ENV_TRIAD_SLOTS)

    search = sub.add_parser("search", help="ranked ENVO candidates with definitions")
    search.add_argument("text")
    search.add_argument("--slot", choices=ENV_TRIAD_SLOTS, help="restrict to this slot's anchor")
    search.add_argument(
        "--within", action="append", metavar="CURIE", help="restrict to this subtree (repeatable)"
    )
    search.add_argument("--limit", type=int, default=10)

    descendants = sub.add_parser("descendants", help="more specific terms below a CURIE")
    descendants.add_argument("curie")
    descendants.add_argument("--limit", type=int, default=50)

    fallback = sub.add_parser("fallback", help="the tier-3 value for this extension and slot")
    fallback.add_argument("interface")
    fallback.add_argument("slot", choices=ENV_TRIAD_SLOTS)

    validate = sub.add_parser("validate", help="gate values before emitting them")
    validate.add_argument("slot", choices=ENV_TRIAD_SLOTS)
    validate.add_argument("values", nargs="+", metavar="VALUE")
    validate.add_argument(
        "--interface",
        help="the MIxS extension in play. Always pass it: allowed prefixes and "
        "value sets are per-interface, so omitting it validates more strictly "
        "than the schema actually does",
    )
    return parser


def run_validate(args: argparse.Namespace) -> int:
    index = get_envo_index()
    interface = resolve_interface(args.interface) if args.interface else None
    failed = False
    for value in args.values:
        result = index.validate(value, args.slot, interface)
        print(f"{'PASS' if result.ok else 'FAIL'}  {value}")
        if result.ok:
            print(f"      source: {result.source}")
        for failure in result.failures:
            print(f"      failed: {failure}")
        for warning in result.warnings:
            print(f"      warning: {warning}")
        if result.corrected_value:
            print(f"      use instead: {result.corrected_value}")
        failed = failed or not result.ok
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    index = get_envo_index()

    if args.command == "biomes":
        values = index.biome_values()
        print(f"# Every ENVO biome ({len(values)}) — the complete env_broad_scale set")
        print(", ".join(values))
    elif args.command == "pool":
        print(index.format_expansion_context(resolve_interface(args.interface), args.slot))
    elif args.command == "search":
        hits = index.search(
            args.text, slot=args.slot, within=tuple(args.within or ()) or None, limit=args.limit
        )
        print(index.format_candidates(hits, header=f"Candidates for {args.text!r}"))
    elif args.command == "descendants":
        term = index.get(args.curie)
        if term is None:
            raise SystemExit(f"{args.curie} is not in ENVO {index.envo_version}")
        values = index.values_under(args.curie, limit=args.limit)
        print(f"# Below {term.value} ({len(values)} shown)")
        print("\n".join(f"- {value}" for value in values) or "(none)")
    elif args.command == "fallback":
        print(index.generic_fallback(resolve_interface(args.interface), args.slot))
    elif args.command == "validate":
        return run_validate(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
