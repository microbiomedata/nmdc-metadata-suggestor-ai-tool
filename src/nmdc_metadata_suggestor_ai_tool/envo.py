"""ENVO lookups and env triad validation.

Ontology access goes through :mod:`oaklib` -- term lookup, obsolescence,
labels, and the subclass hierarchy are all its job, and nothing here
reimplements them. What this module adds is the NMDC-specific layer oaklib has
no opinion about: which ENVO subtree each MIxS env triad slot must point into,
what each submission-schema interface permits, the three-tier resolution the
skill follows, and the gate every suggested value passes before it reaches a
caller.

The adapter is ``sqlite:obo:envo`` (semantic-sql). oaklib downloads and caches
the ontology on first use, so a cold process pays that once; warm, a lookup is
sub-millisecond. Pre-warm it in a container image with::

    python -c "from oaklib import get_adapter; get_adapter('sqlite:obo:envo')"

Usage mirrors :class:`~nmdc_metadata_suggestor_ai_tool.schema_context.SchemaContextBuilder`::

    from nmdc_metadata_suggestor_ai_tool.envo import get_envo

    envo = get_envo()
    envo.search("rhizosphere", slot="env_local_scale")
    envo.validate("soil [ENVO:00001998]", "env_medium", "SoilInterface")
"""

from __future__ import annotations

import functools
import logging
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from oaklib import get_adapter  # type: ignore[import-untyped]
from oaklib.datamodels.search import (  # type: ignore[import-untyped]
    SearchConfiguration,
    SearchProperty,
)
from oaklib.datamodels.vocabulary import IS_A  # type: ignore[import-untyped]
from oaklib.interfaces import OboGraphInterface  # type: ignore[import-untyped]

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_ENV_TRIAD_VALUE_PATTERN,
    ENV_TRIAD_ANCHORS,
    ENV_TRIAD_CURIE_PATTERN,
    ENV_TRIAD_SLOTS,
    ENVO_ADAPTER,
    ENVO_PREFIX,
    HARD_ANCHOR_SLOTS,
)
from nmdc_metadata_suggestor_ai_tool.models.llm_output import (
    LLMOutput,
    MetadataFieldSuggestion,
    TriadProvenance,
    TriadTier,
)
from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import MixsExtensions

logger = logging.getLogger(__name__)

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
# env_broad_scale is absent by design: all of ENVO holds ~130 biome terms, so
# every extension gets the complete list (see `biome_values`) rather than seeds.
#
# Every CURIE here is verified by tests/test_envo.py to exist, to be
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

MAX_DEFINITION_CHARS = 400


@dataclass(frozen=True)
class EnvoTerm:
    """One ENVO class, as this package needs it."""

    curie: str
    label: str
    definition: str | None = None
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
    def source(self) -> TriadTier:
        """The tier this value actually came from, for ``TriadProvenance``."""
        return "submission_enum" if self.in_valueset else "envo_expansion"


class Envo:
    """NMDC env triad rules over an oaklib ENVO adapter."""

    def __init__(self, adapter: OboGraphInterface | None = None) -> None:
        self.adapter = adapter if adapter is not None else get_adapter(ENVO_ADAPTER)
        self._anchor_cache: dict[str, frozenset[str]] = {}

    # -- provenance --------------------------------------------------------

    @functools.cached_property
    def envo_version(self) -> str | None:
        """The ENVO release the adapter is serving, for reports and errors."""
        for ontology in self.adapter.ontologies():
            versions = (self.adapter.ontology_metadata_map(ontology) or {}).get("owl:versionIRI")
            if versions:
                return str(versions[0] if isinstance(versions, list) else versions)
        return None

    # -- lookup ------------------------------------------------------------

    @functools.cached_property
    def _obsolete(self) -> frozenset[str]:
        return frozenset(str(c) for c in self.adapter.obsoletes())

    def get(self, curie: str) -> EnvoTerm | None:
        """Return the term for a CURIE, or None if ENVO does not define it."""
        label = self.adapter.label(curie)
        if label is None:
            return None
        definition = self.adapter.definition(curie)
        return EnvoTerm(
            curie=curie,
            label=label,
            definition=definition[:MAX_DEFINITION_CHARS] if definition else None,
            anchors=tuple(slot for slot in ENV_TRIAD_SLOTS if curie in self._anchor_members(slot)),
            obsolete=curie in self._obsolete,
        )

    def descendants(self, curie: str) -> set[str]:
        """Transitive subclass closure below a CURIE, excluding the term itself."""
        return {
            str(c) for c in self.adapter.descendants(curie, predicates=[IS_A]) if str(c) != curie
        }

    def ancestors(self, curie: str) -> set[str]:
        """Transitive superclasses of a CURIE, excluding the term itself."""
        return {str(c) for c in self.adapter.ancestors(curie, predicates=[IS_A]) if str(c) != curie}

    def is_under(self, curie: str, ancestor: str) -> bool:
        """True when curie is ancestor or descends from it."""
        return curie == ancestor or ancestor in self.ancestors(curie)

    def _anchor_members(self, slot: str) -> frozenset[str]:
        """ENVO terms under a slot's MIxS anchor class.

        Cached because `enforce_env_triad_values` checks membership up to twelve
        times per suggestion; one closure query beats a per-call graph walk.
        """
        if slot not in self._anchor_cache:
            self._anchor_cache[slot] = frozenset(
                c for c in self.descendants(ENV_TRIAD_ANCHORS[slot]) if c.startswith(ENVO_PREFIX)
            )
        return self._anchor_cache[slot]

    def in_anchor(self, curie: str, slot: str) -> bool:
        """True when a CURIE satisfies the slot's MIxS anchor class."""
        return curie == ENV_TRIAD_ANCHORS[slot] or curie in self._anchor_members(slot)

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

        Candidates come from oaklib's search. What is added here is the ordering
        the skill wants -- exact label first, then label edge, word, alias -- and
        a fallback to individual words when the whole phrase finds nothing, so
        "rhizosphere soil" returns rhizosphere and soil terms rather than the
        empty result oaklib gives for a phrase no term is called.
        """
        query = text.strip().lower()
        if not query:
            return []

        scope: set[str] | None = None
        if within:
            scope = set()
            for root in within:
                scope.add(root)
                scope |= self.descendants(root)

        def eligible(curie: str) -> bool:
            if not curie.startswith(ENVO_PREFIX):
                return False
            if not include_obsolete and curie in self._obsolete:
                return False
            if slot is not None and not self.in_anchor(curie, slot):
                return False
            return scope is None or curie in scope

        scored = self._rank(query, eligible)
        words = [w for w in query.split() if len(w) > 2]
        if not scored and len(words) > 1:
            best: dict[str, tuple[int, int, EnvoTerm]] = {}
            for word in words:
                for score, _, curie, term in self._rank(word, eligible):
                    hits, top, _ = best.get(curie, (0, 0, term))
                    best[curie] = (hits + 1, max(top, score), term)
            # Rank by how many query words hit, then how strongly the best one
            # did, so an exact label match on the rarer word ("rhizosphere")
            # outranks an alias match on the common one ("soil").
            scored = [
                (hits * 1000 + top, -len(term.label), curie, term)
                for curie, (hits, top, term) in best.items()
            ]

        scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
        return [row[3] for row in scored[:limit]]

    def _rank(
        self, query: str, eligible: Callable[[str], bool]
    ) -> list[tuple[int, int, str, EnvoTerm]]:
        """Score oaklib's hits for one whole-word query phrase."""
        config = SearchConfiguration(
            properties=[SearchProperty.LABEL, SearchProperty.ALIAS], is_partial=True
        )
        word = re.compile(rf"\b{re.escape(query)}\b")
        scored: list[tuple[int, int, str, EnvoTerm]] = []
        for hit in self.adapter.basic_search(query, config=config):
            curie = str(hit)
            if not eligible(curie):
                continue
            term = self.get(curie)
            if term is None:
                continue
            label = term.label.lower()
            if label == query:
                score = 100
            elif label.startswith(f"{query} ") or label.endswith(f" {query}"):
                score = 80
            elif word.search(label):
                score = 60
            elif self._alias_hit(curie, word):
                score = 40
            else:
                # oaklib's partial search is a substring match, so "air" comes
                # back for "dairy food product". Whole-word only, per the skill.
                continue
            scored.append((score, -len(label), curie, term))
        return scored

    def _alias_hit(self, curie: str, word: re.Pattern[str]) -> bool:
        """True when a synonym matches the query on a word boundary."""
        aliases = self.adapter.entity_alias_map(curie) or {}
        return any(word.search(str(a).lower()) for values in aliases.values() for a in values)

    # -- candidate pools ---------------------------------------------------

    def biome_values(self) -> list[str]:
        """Every non-obsolete ENVO biome, as ``label [CURIE]`` strings.

        All of ENVO holds ~130 biomes, so the complete env_broad_scale universe
        fits in a prompt for any MIxS extension -- no search step needed.
        """
        return self.values_under(ENV_TRIAD_ANCHORS["env_broad_scale"])

    def values_under(self, curie: str, limit: int | None = None) -> list[str]:
        """Non-obsolete ENVO descendants of a CURIE, as ``label [CURIE]`` strings."""
        wanted = [
            c
            for c in self.descendants(curie)
            if c.startswith(ENVO_PREFIX) and c not in self._obsolete
        ]
        labels = dict(self.adapter.labels(wanted))
        values = sorted(f"{labels[c]} [{c}]" for c in wanted if labels.get(c))
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
        term = self.get(curie)
        return term.value if term else curie

    def most_general_value(self, values: frozenset[str] | set[str]) -> str | None:
        """The value in a set that subsumes most of the others, or None.

        A member has to cover more than half the set to count as its broadest
        term. The env_broad_scale and env_medium value sets each have a clear
        root (terrestrial biome covers 51 of 51 soil biomes; soil covers 84 of
        84 soil media), while the env_local_scale sets are flat collections of
        sibling features whose best candidate covers 3 to 7 of 50-plus. Picking
        a winner there would dress an arbitrary choice up as a considered one.

        Non-ENVO values (UBERON, PO) are skipped: the ENVO adapter cannot place
        them in the hierarchy.
        """
        by_curie: dict[str, str] = {}
        for value in values:
            match = ENV_TRIAD_CURIE_PATTERN.match(value)
            if match and match.group(2).startswith(ENVO_PREFIX) and self.get(match.group(2)):
                by_curie[match.group(2)] = value
        if not by_curie:
            return None
        best = min(
            by_curie, key=lambda curie: (-len(self.descendants(curie) & by_curie.keys()), curie)
        )
        subsumed = len(self.descendants(best) & by_curie.keys())
        return by_curie[best] if subsumed * 2 > len(by_curie) - 1 else None

    # -- validation --------------------------------------------------------

    def validate(
        self,
        value: str,
        slot: str,
        interface_name: str | None = None,
        *,
        waive_anchor_in_valueset: bool = True,
    ) -> ValidationResult:
        """Check one ``label [CURIE]`` value against the env triad rules.

        Checks, in order: slot name, the slot's own pattern from the submission
        schema, term exists, term is not obsolete, label matches ENVO's official
        label, and anchor membership. Anchor membership is a hard gate for
        env_broad_scale and env_medium and advisory for env_local_scale (see
        ``HARD_ANCHOR_SLOTS``).

        Where a slot's pattern admits UBERON or PO alongside ENVO -- the
        host-associated and plant/soil/water local scale and medium slots do --
        such a value passes the pattern but cannot be checked further, since the
        adapter only knows ENVO. That is reported as a warning, not a failure.

        The anchor gate polices ENVO expansion, not the schema: a value already
        in the interface's curated set is never rejected for its anchor.
        Existence and obsolescence still apply, so terms the schema still offers
        but ENVO has dropped are caught wherever they appear.

        Set ``waive_anchor_in_valueset=False`` when the caller does not know which
        extension is in play. The waiver exists to respect the extension's own
        curated list; letting some *other* extension's list license an off-anchor
        term is not the same thing. Only two curated values on hard-anchor slots
        depend on the waiver at all, so switching it off is close to free.
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

        if "|" in value:
            # ENVO documents a pipe-separated multi-term form and the schema regex tolerates
            # it by accident, but everything downstream assumes one term. Say so plainly
            # rather than reporting a mangled label.
            return ValidationResult(
                ok=False,
                value=value,
                slot=slot,
                failures=(
                    "contains '|'. ENVO allows several terms per slot, but this pipeline "
                    "emits one; choose the single best term.",
                ),
                in_valueset=in_valueset,
            )

        curie_match = ENV_TRIAD_CURIE_PATTERN.match(value)
        if curie_match is None:  # pragma: no cover - the slot patterns all enforce this shape
            return ValidationResult(
                ok=False, value=value, slot=slot, failures=("could not parse 'label [CURIE]'",)
            )

        label, curie = curie_match.group(1), curie_match.group(2)
        if not curie.startswith(ENVO_PREFIX):
            return ValidationResult(
                ok=True,
                value=value,
                slot=slot,
                warnings=(
                    f"{curie} is allowed by this slot's pattern but is not an ENVO term, "
                    "so it cannot be checked against the ontology",
                ),
                in_valueset=in_valueset,
            )

        term = self.get(curie)
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
            replacement = self.replacement_for(curie)
            hint = f"; ENVO replaced it with {replacement}" if replacement else ""
            failures.append(f"{curie} is obsolete in ENVO{hint}")
            corrected = replacement
        if term.label and label != term.label:
            message, repair = self._reconcile_label(label, curie, term, slot)
            failures.append(message)
            corrected = corrected or repair
        if not self.in_anchor(curie, slot):
            anchor = ENV_TRIAD_ANCHORS[slot]
            anchor_term = self.get(anchor)
            anchor_label = anchor_term.label if anchor_term else anchor
            message = f"{curie} does not descend from {anchor_label} [{anchor}]"
            if slot in HARD_ANCHOR_SLOTS and not (in_valueset and waive_anchor_in_valueset):
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

    def _reconcile_label(
        self, label: str, curie: str, term: EnvoTerm, slot: str
    ) -> tuple[str, str | None]:
        """Decide what a label that is not the CURIE's official label means.

        A model that composes ``label [CURIE]`` instead of copying an entry can pair a label
        from one value-set entry with a CURIE from another. Both halves then name real terms
        and there is no way to tell from the CURIE alone which was meant -- but the label is
        what the model reasoned about and what a reviewer reads, so the label wins where it
        resolves cleanly.

        Returns the failure message and, when the repair is unambiguous, the value to use
        instead. ``None`` means do not guess.
        """
        official = f"label {label!r} does not match the ENVO label {term.label!r}"

        if self._is_alias_of(curie, label):
            return f"{official}; it is a synonym of that term", term.value

        named = [c for c in self.adapter.curies_by_label(label) if c.startswith(ENVO_PREFIX)]
        if len(named) > 1:
            return f"{official}, and {label!r} itself names several ENVO terms: {named}", None
        if not named:
            # The label names nothing in ENVO, so it is drift on an otherwise good CURIE.
            return official, term.value
        if named[0] == curie:  # pragma: no cover - equal labels are handled above
            return official, term.value

        other = named[0]
        repaired = f"{label} [{other}]"
        if self._curie_fits_slot(other, slot, repaired in get_slot_valueset(None, slot)):
            return (
                f"{official}; {label!r} is {other}, so the CURIE and label name different "
                f"terms. Repairing toward the label",
                repaired,
            )
        return (
            f"{official}; {label!r} is {other}, which does not satisfy {slot}, so neither "
            f"half of the value can be trusted",
            None,
        )

    def _is_alias_of(self, curie: str, label: str) -> bool:
        """True when label is a recorded synonym of the term, not a different term."""
        aliases = self.adapter.entity_alias_map(curie) or {}
        return any(str(a).lower() == label.lower() for values in aliases.values() for a in values)

    def _curie_fits_slot(self, curie: str, slot: str, in_valueset: bool) -> bool:
        """Whether a CURIE is usable for a slot, on the same terms `validate` applies."""
        term = self.get(curie)
        if term is None or term.obsolete:
            return False
        return in_valueset or slot not in HARD_ANCHOR_SLOTS or self.in_anchor(curie, slot)

    def replacement_for(self, curie: str) -> str | None:
        """The ``label [CURIE]`` ENVO says supersedes an obsolete term, if any.

        oaklib carries `term replaced by` (IAO:0100001); the previous pinned
        artifact dropped it, so an obsolete term could only be rejected rather
        than redirected.
        """
        metadata = self.adapter.entity_metadata_map(curie) or {}
        for successor in metadata.get("IAO:0100001") or []:
            term = self.get(str(successor))
            if term is not None:
                return term.value
        return None

    # -- prompt formatting -------------------------------------------------

    def format_expansion_context(self, interface_name: str, slot: str) -> str:
        """Describe where ENVO expansion may draw candidates for one slot.

        For env_broad_scale this is the complete biome list. For the other two
        slots it names the seed subtrees (and their sizes) to search within,
        rather than dumping hundreds of terms into the prompt.
        """
        anchor = ENV_TRIAD_ANCHORS[slot]
        anchor_term = self.get(anchor)
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
            term = self.get(seed)
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
        from nmdc_metadata_suggestor_ai_tool.schema_context import get_schema_view

        try:
            slots = {s.name: s for s in get_schema_view().class_induced_slots(interface_name)}
        except ValueError:
            slots = {}
        declared = slots.get(slot)
        if declared is not None and declared.pattern:
            source = str(declared.pattern)
    return re.compile(source)


@functools.lru_cache(maxsize=1)
def get_envo() -> Envo:
    """Load and cache the ENVO adapter with the NMDC env triad rules attached."""
    return Envo()


def enforce_env_triad_values(output: LLMOutput, interface_names: list[str] | None) -> LLMOutput:
    """Drop or repair env triad values that would fail submission portal validation.

    Label drift is corrected in place. Anything that fails a hard check (bad
    pattern, unknown or obsolete CURIE, wrong anchor class) is replaced by the
    extension's generic fallback rather than emitted, so callers never receive an
    invalid value and never receive an empty one.

    ``provenance`` is written from the validation result rather than taken from
    the model, so the tier is a derived fact about the value: which pool it came
    from, whose value set justified that, and whether the gate changed it.

    This is the deterministic gate: both the programmatic pipeline and the
    agentic path run every suggestion through it, so a value cannot reach a
    caller just because the model skipped its own validation step.

    ``interface_names`` of None means the extension is unknown, not that the
    strictest interface applies -- every extension is then treated as possible,
    so a legitimate UBERON or PO value is not rejected for want of context.
    """
    envo = get_envo()
    # The curated-set waiver on the anchor gate applies only when the caller named the
    # extensions; another extension's list must not license an off-anchor term.
    known = bool(interface_names)
    interfaces: list[str | None] = (
        list(interface_names) if interface_names else list(MixsExtensions.__members__)
    )
    fallback_interface = interface_names[0] if interface_names else ""

    for suggestion in output.metadata_fields:
        slot = suggestion.field_name
        if slot not in ENV_TRIAD_SLOTS:
            continue
        # Empty and non-string values were previously skipped, which let them reach the
        # caller untouched and made the promise above false. Neither can satisfy the slot
        # pattern, so send them down the same path as any other invalid value.
        proposed = suggestion.value if isinstance(suggestion.value, str) else ""

        # A value only has to satisfy one of the interfaces in play: the schema
        # patterns differ, and host-associated admits UBERON where soil does not.
        graded = [
            (name, envo.validate(proposed, slot, name, waive_anchor_in_valueset=known))
            for name in interfaces
        ]
        # Prefer an interface that found the value in its curated set. Interfaces
        # iterate in enum order, so taking the first merely-valid result would
        # label soil [ENVO:00001998] as envo_expansion -- BuiltEnvInterface comes
        # first and has no value set at all.
        chosen: tuple[str | None, ValidationResult] = (
            next(((n, r) for n, r in graded if r.ok and r.in_valueset), None)
            or next(((n, r) for n, r in graded if r.ok), None)
            or graded[0]
        )
        iface, result = chosen
        if result.ok:
            suggestion.provenance = TriadProvenance(
                tier=result.source,
                outcome="accepted",
                interface=iface if result.in_valueset else None,
                scoped=known,
            )
            continue

        # Apply a repair only if the repaired value itself validates -- a correction that
        # fixes one failure while leaving another is not a correction.
        repair = next((r.corrected_value for _, r in graded if r.corrected_value), None)
        if repair:
            rechecked = [
                (name, envo.validate(repair, slot, name, waive_anchor_in_valueset=known))
                for name in interfaces
            ]
            fixed = next(((n, r) for n, r in rechecked if r.ok and r.in_valueset), None) or next(
                ((n, r) for n, r in rechecked if r.ok), None
            )
            if fixed is not None:
                fixed_iface, fixed_result = fixed
                logger.info(f"Repaired {slot} value: {suggestion.value!r} -> {repair!r}")
                suggestion.provenance = TriadProvenance(
                    tier=fixed_result.source,
                    outcome="repaired",
                    interface=fixed_iface if fixed_result.in_valueset else None,
                    scoped=known,
                    original_value=proposed,
                )
                suggestion.value = repair
                continue

        fallback = envo.generic_fallback(fallback_interface, slot)
        logger.warning(
            f"Rejected {slot} value {suggestion.value!r} ({'; '.join(result.failures)}); "
            f"falling back to {fallback!r}"
        )
        suggestion.provenance = TriadProvenance(
            tier="generalized",
            outcome="replaced",
            interface=fallback_interface or None,
            scoped=known,
            original_value=proposed or None,
        )
        suggestion.value = fallback
        suggestion.reason = f"{suggestion.reason} [Original suggestion failed ENVO validation.]"

    _enforce_slot_coherence(output, fallback_interface, known)
    return output


def _enforce_slot_coherence(output: LLMOutput, fallback_interface: str, scoped: bool) -> None:
    """Reject one term standing in two slots of the same sample.

    The three slots answer different questions -- which biome, which nearby feature, which
    material -- so one term cannot honestly answer two of them. Per-value checks cannot see
    this: each value is defensible alone. Observed with ``rhizosphere`` returned as both
    env_local_scale and env_medium, where the reason texts contradicted each other.

    ENVO decides which slot keeps it: the term stays where it satisfies the anchor and is
    generalized elsewhere. When it satisfies every slot it was used in, there is nothing to
    arbitrate on, so both are left alone and the clash is logged.
    """
    envo = get_envo()
    by_sample: dict[str, dict[str, list[MetadataFieldSuggestion]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for suggestion in output.metadata_fields:
        slot = suggestion.field_name
        if slot not in ENV_TRIAD_SLOTS or not isinstance(suggestion.value, str):
            continue
        if not suggestion.id or not suggestion.value:
            continue
        match = ENV_TRIAD_CURIE_PATTERN.match(suggestion.value)
        if match:
            by_sample[suggestion.id][match.group(2)].append(suggestion)

    for sample_id, terms in by_sample.items():
        for curie, clashing in terms.items():
            if len(clashing) < 2:
                continue
            fits = [s for s in clashing if envo.in_anchor(curie, s.field_name)]
            if len(fits) != 1:
                logger.warning(
                    f"{sample_id}: {curie} used for "
                    f"{sorted(s.field_name for s in clashing)}; cannot tell which slot it "
                    f"belongs to, leaving both."
                )
                continue
            keep = fits[0]
            for suggestion in clashing:
                if suggestion is keep:
                    continue
                fallback = envo.generic_fallback(fallback_interface, suggestion.field_name)
                logger.warning(
                    f"{sample_id}: {curie} cannot be both {keep.field_name} and "
                    f"{suggestion.field_name}; it satisfies {keep.field_name}, so "
                    f"{suggestion.field_name} falls back to {fallback!r}."
                )
                # Narrowing is lost across the regroup above; only str values are
                # collected into `by_sample`, so this is a str in practice.
                previous = suggestion.value if isinstance(suggestion.value, str) else None
                suggestion.provenance = TriadProvenance(
                    tier="generalized",
                    outcome="replaced",
                    interface=fallback_interface or None,
                    scoped=scoped,
                    original_value=previous,
                )
                suggestion.value = fallback
                suggestion.reason = (
                    f"{suggestion.reason} [Reused the {keep.field_name} term; generalized instead.]"
                )
