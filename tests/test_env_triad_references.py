"""Checks on the env-triad skill's reference files.

These files are written by domain experts rather than by the code, so the ENVO
terms in them get the same treatment as EXPANSION_SEEDS: looked up, not trusted.
A term recalled from memory or from local practice is the failure mode this whole
skill exists to prevent, and a reviewer should not have to know ENVO by heart to
catch one.

Every ENVO CURIE in every reference file must exist and be current. Where the
surrounding line names an env triad slot, the term is also checked against that
slot's rules -- including its official label, when the file writes the full
``label [CURIE]`` form.
"""

import re
from pathlib import Path

import pytest

from nmdc_metadata_suggestor_ai_tool.constants import ENV_TRIAD_SLOTS
from nmdc_metadata_suggestor_ai_tool.envo_index import (
    EXPANSION_SEEDS,
    get_envo_index,
    get_slot_valueset,
)
from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import MixsExtensions

SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "env-triad"
REFS_DIR = SKILL_DIR / "refs"
EXTENSIONS_DIR = REFS_DIR / "extensions"

CURIE_PATTERN = re.compile(r"ENVO:\d{7,8}")
VALUE_PATTERN = re.compile(r"`?([A-Za-z][^`\[\]]*?) \[(ENVO:\d{7,8})\]`?")

# CURIEs the docs name on purpose *because* they are not in ENVO. Adding to this
# set should be a deliberate, reviewed act -- it is the one way to get an unknown
# term past the check below.
DOCUMENTED_COUNTEREXAMPLES = {
    "ENVO:02000139",  # desert spring: still in the water value sets, removed from ENVO
    "ENVO:02000145",  # subterranean lake: same
    "ENVO:03605010",  # proposed ENVO-native NMDC subset root, never landed upstream
    "ENVO:03605015",  # end of that proposed subset range
}


def reference_files() -> list[Path]:
    return sorted(REFS_DIR.glob("*.md")) + sorted(EXTENSIONS_DIR.glob("*.md"))


def cited_curies() -> list[tuple[str, int, str, str | None]]:
    """Every ENVO CURIE in the reference files as (file, line number, curie, slot)."""
    found: list[tuple[str, int, str, str | None]] = []
    for path in reference_files():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            slots = [slot for slot in ENV_TRIAD_SLOTS if slot in line]
            slot = slots[0] if len(slots) == 1 else None
            for curie in CURIE_PATTERN.findall(line):
                found.append((path.name, number, curie, slot))
    return found


def slug_for(interface_name: str) -> str:
    """SoilInterface -> soil, HcrFluidsSwabsInterface -> hcr-fluids-swabs."""
    return re.sub(r"(?<!^)(?=[A-Z])", "-", interface_name.removesuffix("Interface")).lower()


SLUG_TO_INTERFACE = {slug_for(name): name for name in MixsExtensions.__members__}


def interface_for(file_name: str) -> str | None:
    """The interface class a per-extension reference file documents, if any."""
    return SLUG_TO_INTERFACE.get(file_name.removesuffix(".md"))


def in_any_curated_valueset(curie: str, slot: str) -> bool:
    """True if any extension's curated value set offers this CURIE for this slot.

    The anchor gate never overrides the schema's own value sets, so neither does
    this check -- rhizosphere is a curated plant medium that ENVO classes as a
    system, and the docs may legitimately cite it.
    """
    return any(
        curie in value
        for interface_name in MixsExtensions.__members__
        for value in get_slot_valueset(interface_name, slot)
    )


# --- the terms experts write ------------------------------------------------


@pytest.mark.parametrize(
    ("file_name", "line", "curie", "slot"),
    cited_curies(),
    ids=lambda v: str(v).replace(" ", "_")[:40],
)
def test_cited_curie_exists_and_is_current(
    file_name: str, line: int, curie: str, slot: str | None
) -> None:
    if curie in DOCUMENTED_COUNTEREXAMPLES:
        return
    index = get_envo_index()
    term = index.get(curie)
    assert term is not None, (
        f"{file_name}:{line} cites {curie}, which is not in ENVO {index.envo_version}. "
        f"Look it up with `envo_lookup.py search`, or add it to DOCUMENTED_COUNTEREXAMPLES "
        f"if it is named deliberately as a term to avoid."
    )
    assert not term.obsolete, f"{file_name}:{line} cites {curie} ({term.label}), obsolete in ENVO"
    if slot in {"env_broad_scale", "env_medium"} and not in_any_curated_valueset(curie, slot):
        assert index.in_anchor(curie, slot), (
            f"{file_name}:{line} offers {curie} ({term.label}) for {slot}, "
            f"which is not under that slot's anchor class"
        )


def test_full_values_carry_the_official_envo_label() -> None:
    """A `label [CURIE]` written into a reference file must use ENVO's own label."""
    index = get_envo_index()
    drift = []
    for path in reference_files():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            for label, curie in VALUE_PATTERN.findall(line):
                term = index.get(curie)
                if term is not None and term.label and label.strip() != term.label:
                    drift.append(f"{path.name}:{number} {label.strip()!r} should be {term.label!r}")
    assert not drift, "\n".join(drift)


def test_the_curie_scan_actually_found_terms() -> None:
    """Guards the parametrize above from silently collecting nothing."""
    assert len(cited_curies()) >= 5


# --- structure --------------------------------------------------------------


def test_every_extension_has_a_reference_file() -> None:
    """A missing file means the agent silently loses that extension's guidance."""
    documented = {interface_for(path.name) for path in EXTENSIONS_DIR.glob("*.md")}
    assert set(MixsExtensions.__members__) <= documented


def test_no_orphan_reference_files() -> None:
    """A file that maps to no extension is unreachable from the roster."""
    orphans = [
        path.name for path in EXTENSIONS_DIR.glob("*.md") if interface_for(path.name) is None
    ]
    assert not orphans, f"not named after any MIxS extension: {orphans}"


def test_roster_links_to_every_extension_file() -> None:
    roster = (REFS_DIR / "extensions.md").read_text()
    for path in sorted(EXTENSIONS_DIR.glob("*.md")):
        assert f"extensions/{path.name}" in roster, f"{path.name} is not linked from the roster"


@pytest.mark.parametrize("path", sorted(EXTENSIONS_DIR.glob("*.md")), ids=lambda p: p.name)
def test_extension_file_keeps_the_expected_sections(path: Path) -> None:
    """Experts write into a fixed skeleton so the agent can rely on the shape."""
    headings = {line.strip() for line in path.read_text().splitlines() if line.startswith("## ")}
    assert headings >= {
        "## Where candidates come from",
        "## Evidence in the sample record",
        "## Domain notes",
        "## Worked examples",
    }, f"{path.name} is missing expected sections; has {sorted(headings)}"


@pytest.mark.parametrize(
    ("interface_name", "curie"),
    [
        (name, curie)
        for name, slots in EXPANSION_SEEDS.items()
        for c in slots.values()
        for curie in c
    ],
    ids=lambda v: str(v),
)
def test_seeded_extensions_point_at_their_pool_command(interface_name: str, curie: str) -> None:
    """An extension with seeds must tell the reader how to see them."""
    slug = slug_for(interface_name)
    text = (EXTENSIONS_DIR / f"{slug}.md").read_text()
    assert "envo_lookup.py pool" in text, f"{slug}.md has seeds but no pool command"
