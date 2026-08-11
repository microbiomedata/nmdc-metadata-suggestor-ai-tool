"""Pinned ENVO lookup and env triad validation.

Backs the ENVO-expansion tier of the ``env-triad`` skill: when the submission
schema's curated value set for a MIxS extension has nothing appropriate -- or,
for the eight extensions that ship no value set at all, from the start -- this
module supplies verified ENVO candidates and gates every value before it is
emitted.

The index is a pinned artifact built by ``scripts/build_envo_index.py`` and
shipped in ``data/envo_index.json.gz``. Reading it needs only the standard
library and never touches the network, so the agent is never pointed at a URL.
Refresh it deliberately with ``make build-envo-index``.

Usage mirrors :class:`~nmdc_metadata_suggestor_ai_tool.schema_context.SchemaContextBuilder`::

    from nmdc_metadata_suggestor_ai_tool.envo_index import get_envo_index

    index = get_envo_index()
    index.search("rhizosphere", slot="env_local_scale")
    index.validate("soil [ENVO:00001998]", "env_medium")
"""

from __future__ import annotations

import functools
import gzip
import importlib.resources
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_ENV_TRIAD_VALUE_PATTERN,
    ENV_TRIAD_ANCHORS,
    ENV_TRIAD_CURIE_PATTERN,
    ENV_TRIAD_SLOTS,
    HARD_ANCHOR_SLOTS,
)

INDEX_RESOURCE = "envo_index.json.gz"

# MIxS extensions whose env triad slots carry a curated value set in
# nmdc-submission-schema. Everything else has env triad slots but no valueset,
# so ENVO expansion is the only grounding available.
CURATED_INTERFACES: frozenset[str] = frozenset(
    {"SoilInterface", "WaterInterface", "SedimentInterface", "PlantAssociatedInterface"}
)

# Where ENVO expansion starts for extensions with no curated value set. Each
# tuple is ordered most-specific-intent first; the first entry doubles as the
# generic fallback when nothing more specific is defensible. A slot with no
# entry expands over its whole anchor subtree.
#
# env_broad_scale is absent by design: all of ENVO holds 127 biome terms, so
# every extension gets the complete list (see `biome_values`) rather than seeds.
#
# Every CURIE here is verified by tests/test_envo_index.py to exist, to be
# non-obsolete, and to sit under its slot's anchor -- these are looked up, not
# recalled.
EXPANSION_SEEDS: dict[str, dict[str, tuple[str, ...]]] = {
    "AirInterface": {
        "env_medium": ("ENVO:00002005", "ENVO:01000797"),  # air, gaseous environmental material
    },
    "BiofilmInterface": {
        "env_medium": ("ENVO:01000156",),  # biofilm material
    },
    "BuiltEnvInterface": {
        "env_local_scale": ("ENVO:00000073",),  # building
        "env_medium": ("ENVO:00002008",),  # dust
    },
    "HcrCoresInterface": {
        "env_medium": ("ENVO:2000045", "ENVO:00002984"),  # hydrocarbon-based material, petroleum
    },
    "HcrFluidsSwabsInterface": {
        "env_medium": ("ENVO:2000045", "ENVO:00002984", "ENVO:00002985"),  # ... , oil
    },
    "WastewaterSludgeInterface": {
        "env_medium": (
            "ENVO:00002044",  # sludge
            "ENVO:00002001",  # waste water
            "ENVO:00002018",  # sewage
            "ENVO:00002059",  # biosolids
        ),
    },
    # HostAssociatedInterface and MiscEnvsInterface intentionally have no seeds:
    # their sampled material is not constrained to any one ENVO subtree, so
    # expansion runs over the whole environmental-material anchor.
}


@dataclass(frozen=True)
class EnvoTerm:
    """One ENVO class from the pinned index."""

    curie: str
    label: str
    definition: str | None = None
    synonyms: tuple[str, ...] = ()
    parents: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    obsolete: bool = False

    @property
    def value(self) -> str:
        """The term as an env triad slot value: ``label [CURIE]``."""
        return f"{self.label} [{self.curie}]"


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating one env triad value."""

    ok: bool
    value: str
    slot: str
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    term: EnvoTerm | None = None
    corrected_value: str | None = None
    in_valueset: bool = False

    def __bool__(self) -> bool:
        return self.ok

    @property
    def source(self) -> str:
        """The tier this value actually came from, for ``MetadataFieldSuggestion``."""
        return "submission_enum" if self.in_valueset else "envo_expansion"


@dataclass
class EnvoIndex:
    """Read-only view over the pinned ENVO index."""

    envo_version: str | None
    terms: dict[str, EnvoTerm]
    _children: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set), repr=False)

    def __post_init__(self) -> None:
        children: dict[str, set[str]] = defaultdict(set)
        for curie, term in self.terms.items():
            for parent in term.parents:
                children[parent].add(curie)
        self._children = children

    # -- lookup ------------------------------------------------------------

    def get(self, curie: str) -> EnvoTerm | None:
        """Return the term for a CURIE, or None if ENVO does not define it."""
        return self.terms.get(curie)

    def descendants(self, curie: str) -> set[str]:
        """Return the transitive subclass closure below a CURIE."""
        seen: set[str] = set()
        stack = [curie]
        while stack:
            for child in self._children.get(stack.pop(), ()):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    def ancestors(self, curie: str) -> set[str]:
        """Return the transitive superclasses of a CURIE."""
        seen: set[str] = set()
        stack = [curie]
        while stack:
            term = self.terms.get(stack.pop())
            if term is None:
                continue
            for parent in term.parents:
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
        return seen

    def is_under(self, curie: str, ancestor: str) -> bool:
        """True when curie is ancestor or descends from it."""
        return curie == ancestor or ancestor in self.ancestors(curie)

    def in_anchor(self, curie: str, slot: str) -> bool:
        """True when a CURIE satisfies the slot's MIxS anchor class."""
        anchor = ENV_TRIAD_ANCHORS[slot]
        term = self.terms.get(curie)
        if term is None:
            return False
        return curie == anchor or slot in term.anchors

    # -- search ------------------------------------------------------------

    def search(
        self,
        text: str,
        slot: str | None = None,
        within: tuple[str, ...] | list[str] | None = None,
        limit: int = 10,
        include_obsolete: bool = False,
    ) -> list[EnvoTerm]:
        """Rank ENVO terms matching text, scoped to a slot anchor and/or subtrees.

        Matching is on whole words in labels and synonyms, so "air" does not
        match "dairy". The whole phrase is tried first, ranking exact label >
        label edge > label word > synonym. Only if that finds nothing does a
        multi-word query fall back to matching its individual words, ranked by
        how many hit -- so "rhizosphere soil", which is no term's label, still
        returns rhizosphere and soil terms rather than nothing.
        """
        query = text.strip().lower()
        if not query:
            return []

        candidates = [
            (curie, term)
            for curie, term in self.terms.items()
            if (include_obsolete or not term.obsolete)
            and (slot is None or self.in_anchor(curie, slot))
        ]
        if within:
            scope: set[str] = set()
            for root in within:
                scope.add(root)
                scope |= self.descendants(root)
            candidates = [row for row in candidates if row[0] in scope]

        scored = self._score(candidates, query)
        words = [word for word in query.split() if len(word) > 2]
        if not scored and len(words) > 1:
            per_word = [self._score(candidates, word) for word in words]
            best: dict[str, tuple[int, int, EnvoTerm]] = {}
            for row in (row for word_rows in per_word for row in word_rows):
                score, _, curie, term = row
                hits, top, _ = best.get(curie, (0, 0, term))
                best[curie] = (hits + 1, max(top, score), term)
            # Rank by how many query words hit, then by how strongly the best one
            # did -- so an exact label match on the rarer word ("rhizosphere")
            # outranks a synonym match on the common one ("soil").
            scored = [
                (hits * 1000 + top, -len(term.label or ""), curie, term)
                for curie, (hits, top, term) in best.items()
            ]

        scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
        return [row[3] for row in scored[:limit]]

    def _score(
        self, candidates: list[tuple[str, EnvoTerm]], query: str
    ) -> list[tuple[int, int, str, EnvoTerm]]:
        """Score candidates against one whole-word query phrase."""
        word_pattern = re.compile(rf"\b{re.escape(query)}\b")
        scored: list[tuple[int, int, str, EnvoTerm]] = []
        for curie, term in candidates:
            label = (term.label or "").lower()
            if label == query:
                score = 100
            elif label.startswith(f"{query} ") or label.endswith(f" {query}"):
                score = 80
            elif word_pattern.search(label):
                score = 60
            elif any(word_pattern.search(syn.lower()) for syn in term.synonyms):
                score = 40
            else:
                continue
            scored.append((score, -len(label), curie, term))
        return scored

    # -- candidate pools ---------------------------------------------------

    def biome_values(self) -> list[str]:
        """Every non-obsolete ENVO biome, as ``label [CURIE]`` strings.

        All of ENVO holds ~127 biomes, so the complete env_broad_scale universe
        fits in a prompt for any MIxS extension -- no search step needed.
        """
        return self.values_under(ENV_TRIAD_ANCHORS["env_broad_scale"])

    def values_under(self, curie: str, limit: int | None = None) -> list[str]:
        """Non-obsolete descendants of a CURIE, as ``label [CURIE]`` strings."""
        values = sorted(
            term.value
            for child in self.descendants(curie)
            if (term := self.terms.get(child)) is not None and not term.obsolete
        )
        return values[:limit] if limit is not None else values

    def expansion_seeds(self, interface_name: str, slot: str) -> tuple[str, ...]:
        """Seed CURIEs for ENVO expansion, or () to expand over the whole anchor."""
        return EXPANSION_SEEDS.get(interface_name, {}).get(slot, ())

    def generic_fallback(self, interface_name: str, slot: str) -> str:
        """The most generic defensible value for a slot, used by the tier-3 path.

        Prefers the broadest term in the extension's curated value set, then its
        first expansion seed, and only falls back to the bare anchor class -- a
        technically valid but near-contentless suggestion -- when there is
        neither.
        """
        curated = self.most_general_value(get_slot_valueset(interface_name, slot))
        if curated:
            return curated
        seeds = self.expansion_seeds(interface_name, slot)
        curie = seeds[0] if seeds else ENV_TRIAD_ANCHORS[slot]
        term = self.terms.get(curie)
        return term.value if term else curie

    def most_general_value(self, values: frozenset[str] | set[str]) -> str | None:
        """The value in a set that subsumes most of the others, or None.

        A member has to cover more than half the set to count as its broadest
        term. The env_broad_scale and env_medium value sets each have a clear
        root (terrestrial biome covers 51 of 51 soil biomes; soil covers 84 of
        84 soil media), while the env_local_scale sets are flat collections of
        sibling features whose best candidate covers 3 to 7 of 50-plus. Picking
        a winner there would dress an arbitrary choice up as a considered one.

        Non-ENVO values (UBERON, PO) are skipped: they are outside the index, so
        their place in the hierarchy is unknown.
        """
        by_curie: dict[str, str] = {}
        for value in values:
            match = ENV_TRIAD_CURIE_PATTERN.match(value)
            if match and match.group(2) in self.terms:
                by_curie[match.group(2)] = value
        if not by_curie:
            return None
        best = min(
            by_curie,
            key=lambda curie: (-len(self.descendants(curie) & by_curie.keys()), curie),
        )
        subsumed = len(self.descendants(best) & by_curie.keys())
        return by_curie[best] if subsumed * 2 > len(by_curie) - 1 else None

    # -- validation --------------------------------------------------------

    def validate(
        self, value: str, slot: str, interface_name: str | None = None
    ) -> ValidationResult:
        """Check one ``label [CURIE]`` value against the env triad rules.

        Checks, in order: slot name, the slot's own pattern from the submission
        schema, term exists, term is not obsolete, label matches ENVO's official
        label, and anchor membership. Anchor membership is a hard gate for
        env_broad_scale and env_medium and advisory for env_local_scale (see
        ``HARD_ANCHOR_SLOTS``).

        Where a slot's pattern admits UBERON or PO alongside ENVO -- the
        host-associated and plant/soil/water local scale and medium slots do --
        such a value passes the pattern but cannot be checked further, since only
        ENVO is indexed. That is reported as a warning, not a failure.

        The anchor gate polices ENVO expansion, not the schema: a value already in
        the interface's curated set is never rejected for its anchor. Existence
        and obsolescence still apply, so dead CURIEs the schema still offers are
        caught wherever they appear.
        """
        if slot not in ENV_TRIAD_SLOTS:
            return ValidationResult(
                ok=False, value=value, slot=slot, failures=(f"{slot!r} is not an env triad slot",)
            )

        failures: list[str] = []
        warnings: list[str] = []
        in_valueset = value in get_slot_valueset(interface_name, slot)

        pattern = get_slot_pattern(interface_name, slot)
        if pattern.match(value) is None:
            return ValidationResult(
                ok=False,
                value=value,
                slot=slot,
                failures=(
                    f"does not match the pattern the submission schema declares for "
                    f"this slot: {pattern.pattern}",
                ),
                in_valueset=in_valueset,
            )

        curie_match = ENV_TRIAD_CURIE_PATTERN.match(value)
        if curie_match is None:  # pragma: no cover - the slot patterns all enforce this shape
            return ValidationResult(
                ok=False, value=value, slot=slot, failures=("could not parse 'label [CURIE]'",)
            )

        label, curie = curie_match.group(1), curie_match.group(2)
        if not curie.startswith("ENVO:"):
            return ValidationResult(
                ok=True,
                value=value,
                slot=slot,
                warnings=(
                    f"{curie} is allowed by this slot's pattern but is not an ENVO term, "
                    "so it cannot be checked against the pinned index",
                ),
                in_valueset=in_valueset,
            )

        term = self.terms.get(curie)
        if term is None:
            return ValidationResult(
                ok=False,
                value=value,
                slot=slot,
                failures=(f"{curie} is not in ENVO {self.envo_version or '(unknown release)'}",),
                in_valueset=in_valueset,
            )

        corrected: str | None = None
        if term.obsolete:
            failures.append(f"{curie} is obsolete in ENVO")
        if term.label and label != term.label:
            failures.append(f"label {label!r} does not match the ENVO label {term.label!r}")
            corrected = term.value
        if not self.in_anchor(curie, slot):
            anchor = ENV_TRIAD_ANCHORS[slot]
            anchor_term = self.terms.get(anchor)
            anchor_label = anchor_term.label if anchor_term else anchor
            message = f"{curie} does not descend from {anchor_label} [{anchor}]"
            if slot in HARD_ANCHOR_SLOTS and not in_valueset:
                failures.append(message)
            else:
                warnings.append(message)

        return ValidationResult(
            ok=not failures,
            value=value,
            slot=slot,
            failures=tuple(failures),
            warnings=tuple(warnings),
            term=term,
            corrected_value=corrected,
            in_valueset=in_valueset,
        )

    # -- prompt formatting -------------------------------------------------

    def format_candidates(self, terms: list[EnvoTerm], header: str | None = None) -> str:
        """Format search hits as Markdown with definitions, for an LLM prompt."""
        lines: list[str] = [f"## {header}"] if header else []
        if not terms:
            lines.append("(no candidates)")
            return "\n".join(lines)
        for term in terms:
            lines.append(f"- {term.value}")
            if term.definition:
                lines.append(f"  {term.definition}")
        return "\n".join(lines)

    def format_expansion_context(self, interface_name: str, slot: str) -> str:
        """Describe where ENVO expansion may draw candidates for one slot.

        For env_broad_scale this is the complete biome list. For the other two
        slots it names the seed subtrees (and their sizes) to search within,
        rather than dumping hundreds of terms into the prompt.
        """
        anchor = ENV_TRIAD_ANCHORS[slot]
        anchor_term = self.terms.get(anchor)
        anchor_value = anchor_term.value if anchor_term else anchor
        lines = [f"## {slot} — ENVO expansion pool ({interface_name})"]

        if slot == "env_broad_scale":
            values = self.biome_values()
            lines.append(f"All {len(values)} ENVO biomes (complete universe for this slot):")
            lines.append(", ".join(values))
            return "\n".join(lines)

        seeds = self.expansion_seeds(interface_name, slot)
        if not seeds:
            lines.append(
                f"No extension-specific subtree. Search the whole {anchor_value} anchor "
                f"({len(self.values_under(anchor))} non-obsolete ENVO terms)."
            )
            return "\n".join(lines)

        lines.append(f"Search within these subtrees (all under {anchor_value}):")
        for seed in seeds:
            term = self.terms.get(seed)
            if term is None:
                continue
            lines.append(f"- {term.value} — {len(self.values_under(seed))} descendants")
            if term.definition:
                lines.append(f"  {term.definition}")
        lines.append(f"Generic fallback: {self.generic_fallback(interface_name, slot)}")
        return "\n".join(lines)


@functools.lru_cache(maxsize=64)
def get_slot_valueset(interface_name: str | None, slot: str) -> frozenset[str]:
    """Return the curated permissible values for one slot, or an empty set.

    Eight of the twelve env-triad-bearing interfaces declare no value set, so an
    empty result is the common case, not an error.
    """
    if not interface_name:
        return frozenset()
    from nmdc_metadata_suggestor_ai_tool.schema_context import get_schema_view

    schema_view = get_schema_view()
    try:
        slots = {s.name: s for s in schema_view.class_induced_slots(interface_name)}
    except ValueError:
        return frozenset()
    declared = slots.get(slot)
    for item in (declared.any_of or []) if declared is not None else []:
        enum_definition = schema_view.get_enum(item.range) if item.range else None
        if enum_definition is not None:
            return frozenset(str(pv.text) for pv in enum_definition.permissible_values.values())
    return frozenset()


@functools.lru_cache(maxsize=64)
def get_slot_pattern(interface_name: str | None, slot: str) -> re.Pattern[str]:
    """Return the value pattern the submission schema declares for one slot.

    Allowed prefixes vary by interface: most slots are ENVO-only, but
    host-associated admits UBERON and plant-associated/soil/water admit PO on
    their local scale and medium slots. Falls back to the ENVO-only default when
    the interface is unknown -- the strictest of the three, so a value that
    passes without an interface passes with one.
    """
    source = DEFAULT_ENV_TRIAD_VALUE_PATTERN
    if interface_name:
        # Imported lazily: validation of a plain ENVO value should not pay for a
        # SchemaView load.
        from nmdc_metadata_suggestor_ai_tool.schema_context import get_schema_view

        try:
            slots = {s.name: s for s in get_schema_view().class_induced_slots(interface_name)}
        except ValueError:
            slots = {}
        declared = slots.get(slot)
        if declared is not None and declared.pattern:
            source = str(declared.pattern)
    return re.compile(source)


def _load_terms(raw: dict[str, Any]) -> dict[str, EnvoTerm]:
    return {
        curie: EnvoTerm(
            curie=curie,
            label=entry.get("label") or curie,
            definition=entry.get("definition"),
            synonyms=tuple(entry.get("synonyms", ())),
            parents=tuple(entry.get("parents", ())),
            anchors=tuple(entry.get("anchors", ())),
            obsolete=bool(entry.get("obsolete")),
        )
        for curie, entry in raw["terms"].items()
    }


@functools.lru_cache(maxsize=1)
def get_envo_index() -> EnvoIndex:
    """Load and cache the pinned ENVO index shipped with this package."""
    resource = importlib.resources.files("nmdc_metadata_suggestor_ai_tool.data").joinpath(
        INDEX_RESOURCE
    )
    with importlib.resources.as_file(resource) as path:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            raw = json.load(handle)
    return EnvoIndex(envo_version=raw.get("envo_version"), terms=_load_terms(raw))
