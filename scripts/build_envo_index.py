"""Build the pinned ENVO index shipped with this package.

Build-time only. Fetches exactly one hard-coded URL (the ENVO OBO Graph JSON
release) and writes a compact, gzipped index of every ENVO class, tagged with
which MIxS env triad anchor class it descends from.

Runtime code (``nmdc_metadata_suggestor_ai_tool.envo_index``) reads the
generated artifact and never touches the network, so the agent is never pointed
at a URL. Regenerate deliberately, when ENVO cuts a release:

    make build-envo-index

Then review the diff in ``envo_version`` and the term counts before committing.

Why an ENVO-only DAG: the ENVO release graph mixes in imported classes from
other ontologies (FOODON, CHEBI, PATO). The env triad slots accept only
``ENVO:`` CURIEs, so those imports are dropped from the index. To keep the
subclass hierarchy connected after dropping them, each ENVO class stores its
*nearest ENVO ancestors* -- walking up through any non-ENVO intermediates --
rather than its literal parents. Anchor membership is computed on the full
graph before the trim, so the two agree exactly.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import urllib.request
from pathlib import Path
from typing import Any

ENVO_JSON_URL = "https://purl.obolibrary.org/obo/envo.json"

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "nmdc_metadata_suggestor_ai_tool"
    / "data"
    / "envo_index.json.gz"
)

# MIxS anchor class per env triad slot. See the slot descriptions in
# nmdc-submission-schema: env_broad_scale recommends subclasses of biome,
# env_medium subclasses of environmental material.
ANCHORS = {
    "env_broad_scale": "ENVO:00000428",  # biome
    "env_local_scale": "ENVO:01000813",  # astronomical body part
    "env_medium": "ENVO:00010483",  # environmental material
}

SYNONYM_PREDICATES = {"hasExactSynonym", "hasNarrowSynonym"}

MAX_DEFINITION_CHARS = 400
MAX_SYNONYMS = 8


def iri_to_curie(iri: str) -> str | None:
    """Convert an OBO PURL to a CURIE, or None for anything else."""
    if "/obo/" not in iri:
        return None
    return iri.rsplit("/", 1)[-1].replace("_", ":", 1)


def load_graph(source: str) -> dict[str, Any]:
    """Load the ENVO OBO Graph JSON from a URL or a local path."""
    if source.startswith("https://"):
        with urllib.request.urlopen(source, timeout=120) as response:  # noqa: S310
            payload = json.load(response)
    else:
        with open(source, encoding="utf-8") as handle:
            payload = json.load(handle)
    return payload["graphs"][0]


def collect_nodes(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract class nodes keyed by CURIE, including non-ENVO imports."""
    nodes: dict[str, dict[str, Any]] = {}
    for node in graph["nodes"]:
        if node.get("type") != "CLASS":
            continue
        curie = iri_to_curie(node["id"])
        if curie is None:
            continue
        meta = node.get("meta") or {}
        nodes[curie] = {
            "label": node.get("lbl"),
            "definition": (meta.get("definition") or {}).get("val"),
            "synonyms": [
                syn["val"] for syn in meta.get("synonyms", []) if syn.get("pred") in SYNONYM_PREDICATES
            ],
            "obsolete": bool(meta.get("deprecated")),
        }
    return nodes


def collect_parents(graph: dict[str, Any]) -> dict[str, set[str]]:
    """Extract the is_a edge set as child CURIE -> parent CURIEs."""
    parents: dict[str, set[str]] = collections.defaultdict(set)
    for edge in graph["edges"]:
        if edge["pred"] != "is_a":
            continue
        child = iri_to_curie(edge["sub"])
        parent = iri_to_curie(edge["obj"])
        if child and parent:
            parents[child].add(parent)
    return parents


def descendants(parents: dict[str, set[str]], root: str) -> set[str]:
    """Return the transitive subclass closure of root over the full graph."""
    children: dict[str, set[str]] = collections.defaultdict(set)
    for child, parent_set in parents.items():
        for parent in parent_set:
            children[parent].add(child)
    seen: set[str] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        for child in children.get(current, ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def nearest_envo_parents(parents: dict[str, set[str]], curie: str) -> set[str]:
    """Walk up through non-ENVO intermediates to the closest ENVO ancestors."""
    found: set[str] = set()
    seen: set[str] = set()
    stack = list(parents.get(curie, ()))
    while stack:
        candidate = stack.pop()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.startswith("ENVO:"):
            found.add(candidate)
        else:
            stack.extend(parents.get(candidate, ()))
    return found


def build_index(graph: dict[str, Any]) -> dict[str, Any]:
    """Build the shipped index from an ENVO OBO Graph JSON graph."""
    nodes = collect_nodes(graph)
    parents = collect_parents(graph)
    anchor_members = {slot: descendants(parents, root) for slot, root in ANCHORS.items()}

    terms: dict[str, dict[str, Any]] = {}
    for curie in sorted(nodes):
        if not curie.startswith("ENVO:"):
            continue
        node = nodes[curie]
        entry: dict[str, Any] = {
            "label": node["label"],
            "parents": sorted(nearest_envo_parents(parents, curie)),
        }
        if node["definition"]:
            entry["definition"] = node["definition"][:MAX_DEFINITION_CHARS]
        if node["synonyms"]:
            entry["synonyms"] = node["synonyms"][:MAX_SYNONYMS]
        anchors = [slot for slot, members in anchor_members.items() if curie in members]
        if anchors:
            entry["anchors"] = anchors
        if node["obsolete"]:
            entry["obsolete"] = True
        terms[curie] = entry

    return {
        "envo_version": (graph.get("meta") or {}).get("version"),
        "anchors": ANCHORS,
        "terms": terms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=ENVO_JSON_URL,
        help=f"ENVO OBO Graph JSON URL or local path (default: {ENVO_JSON_URL})",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help=f"Output path (default: {DEFAULT_OUTPUT})"
    )
    args = parser.parse_args()

    print(f"Reading {args.source}")
    index = build_index(load_graph(args.source))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(index, separators=(",", ":"), sort_keys=True).encode("utf-8")
    # mtime=0 keeps the artifact byte-reproducible, so rebuilding an unchanged
    # ENVO release produces no diff to review.
    with open(args.output, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as handle:
            handle.write(payload)

    counts = collections.Counter(
        slot for entry in index["terms"].values() for slot in entry.get("anchors", [])
    )
    size_mb = args.output.stat().st_size / 1e6
    print(f"ENVO release: {index['envo_version']}")
    print(f"Terms: {len(index['terms'])}")
    for slot in ANCHORS:
        print(f"  under {slot} anchor {ANCHORS[slot]}: {counts[slot]}")
    print(f"Wrote {args.output} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
