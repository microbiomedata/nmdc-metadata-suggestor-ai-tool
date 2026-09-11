"""Score env triad suggestions against reference triads.

Nothing here decides whether a value is *good*; that is the ontology-aware
scorer in ``nmdc-ai-eval``. This module handles what that scorer does not: the
two dialects reference triads arrive in, joining references to samples, reading
a pipeline output back by sample id, and comparing two runs by direction of
change.

Reference values arrive in two dialects. NMDC biosample records carry
``has_raw_value`` strings such as ``"agricultural soil [ENVO:00002259]"``. Author
supplied supplement tables carry whatever the authors typed:
``"agricultural soil [ENVO_00002259] | plant matter [ENVO_01001121]"``, with
underscored CURIEs, capitalised labels, and pipe-joined multi-terms. Both are
parsed into :class:`TriadTerm` and compared on the CURIE, so the two dialects
meet in one place and a label difference never masks a CURIE match.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from nmdc_metadata_suggestor_ai_tool.constants import ENV_TRIAD_SLOTS
from nmdc_metadata_suggestor_ai_tool.models.llm_output import (
    LLMOutput,
    MetadataFieldSuggestion,
)

# "label [PREFIX:0000000]" or "label [PREFIX_0000000]"; the label may be empty.
TERM_PATTERN = re.compile(r"^\s*(?P<label>.*?)\s*\[(?P<prefix>[A-Za-z]+)[:_](?P<local>\d+)\]\s*$")
MULTI_TERM_SEPARATOR = "|"

# reference name -> sample id -> slot -> terms
Reference = dict[str, dict[str, list["TriadTerm"]]]


@dataclass(frozen=True)
class TriadTerm:
    """One ontology term as written in a triad value, normalized for comparison."""

    label: str
    curie: str

    @property
    def value(self) -> str:
        """The term in the portal's ``label [CURIE]`` form."""
        return f"{self.label} [{self.curie}]" if self.curie else self.label


def parse_triad_value(raw: str | None) -> list[TriadTerm]:
    """Split a raw triad cell into its terms.

    ``|`` separates multiple terms. CURIEs are normalized to ``PREFIX:local``,
    so ``ENVO_00000446`` and ``ENVO:00000446`` compare equal. A piece with no
    bracketed CURIE keeps its text as the label and an empty ``curie``. Empty
    and ``NA`` cells yield no terms.
    """
    if not raw:
        return []
    terms: list[TriadTerm] = []
    for piece in raw.split(MULTI_TERM_SEPARATOR):
        piece = piece.strip()
        if not piece or piece.upper() in {"NA", "N/A", "NOT APPLICABLE"}:
            continue
        match = TERM_PATTERN.match(piece)
        if match is None:
            terms.append(TriadTerm(label=piece, curie=""))
            continue
        curie = f"{match['prefix'].upper()}:{match['local']}"
        terms.append(TriadTerm(label=match["label"].strip(), curie=curie))
    return terms


def reference_from_rows(
    rows: Iterable[dict[str, str]], *, id_column: str = "sample_name"
) -> Reference:
    """Build a reference from tabular rows, e.g. a supplement CSV read by DictReader.

    Rows are keyed by ``id_column``; rows with an empty key are ignored. Only the
    triad columns present in a row contribute.
    """
    reference: Reference = {}
    for row in rows:
        sample_id = (row.get(id_column) or "").strip()
        if not sample_id:
            continue
        reference[sample_id] = {
            slot: parse_triad_value(row.get(slot)) for slot in ENV_TRIAD_SLOTS if slot in row
        }
    return reference


def biosample_raw_value(record: dict, slot: str) -> str | None:
    """Read a triad slot from an NMDC biosample record.

    Prefers ``has_raw_value``; falls back to composing the value from
    ``term.name`` and ``term.id`` when only the structured form is present.
    """
    value = record.get(slot)
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    raw = value.get("has_raw_value")
    if isinstance(raw, str) and raw.strip():
        return raw
    term = value.get("term")
    if isinstance(term, dict) and term.get("id"):
        return f"{term.get('name', '')} [{term['id']}]".strip()
    return None


def reference_from_biosamples(records: Iterable[dict]) -> Reference:
    """Build a reference from NMDC biosample records, keyed by their ``id``."""
    reference: Reference = {}
    for record in records:
        sample_id = record.get("id")
        if not sample_id:
            continue
        reference[str(sample_id)] = {
            slot: parse_triad_value(biosample_raw_value(record, slot)) for slot in ENV_TRIAD_SLOTS
        }
    return reference


def rekey_reference(reference: Reference, mapping: dict[str, str]) -> Reference:
    """Return *reference* keyed by ``mapping[old_key]``, dropping unmapped rows.

    Supplement tables key on the authors' sample names while suggestions key on
    NMDC ids; this joins the two once so the scorer never sees both.
    """
    return {mapping[key]: slots for key, slots in reference.items() if key in mapping}


def triad_suggestions(output: LLMOutput) -> dict[tuple[str, str], MetadataFieldSuggestion]:
    """Index an output's triad suggestions by ``(sample id, slot)``.

    A later suggestion for the same key replaces an earlier one, matching how a
    consumer that walks the list would end up.
    """
    return {
        (suggestion.id or "", suggestion.field_name): suggestion
        for suggestion in output.metadata_fields
        if suggestion.field_name in ENV_TRIAD_SLOTS
    }


def suggested_term(suggestion: MetadataFieldSuggestion) -> TriadTerm | None:
    """The suggestion's value as a term, or None when it is not a string value."""
    if not isinstance(suggestion.value, str):
        return None
    terms = parse_triad_value(suggestion.value)
    return terms[0] if terms else None


def matches(term: TriadTerm | None, reference_terms: list[TriadTerm]) -> tuple[bool, bool]:
    """Return ``(curie_match, label_match)`` of *term* against a reference cell.

    A cell holding several terms counts as matched by any one of them. Labels
    compare case-insensitively, which is exactly the slack the supplement's
    ``Terrestrial Biome`` needs against ENVO's ``terrestrial biome``.
    """
    if term is None or not reference_terms:
        return False, False
    curie_match = bool(term.curie) and any(term.curie == ref.curie for ref in reference_terms)
    label_match = any(term.label.casefold() == ref.label.casefold() for ref in reference_terms)
    return curie_match, label_match


def coverage(output: LLMOutput, sample_ids: Iterable[str]) -> dict[str, int]:
    """How many samples got a triad back, and how many suggestions carry no usable id.

    The model sometimes drops the id field for a whole chunk, or folds a chunk
    into one unlabeled triad. Those suggestions cannot be scored, and they are
    counted here so a low score is not mistaken for a wrong answer.
    """
    known = set(sample_ids)
    labeled = {f.id for f in output.metadata_fields if f.id in known}
    return {
        "n_samples": len(known),
        "samples_with_suggestions": len(labeled),
        "suggestions_total": len(output.metadata_fields),
        "suggestions_without_id": sum(1 for f in output.metadata_fields if not f.id),
        "suggestions_with_unknown_id": sum(
            1 for f in output.metadata_fields if f.id and f.id not in known
        ),
    }


Scorer = Callable[[TriadTerm | None, list[TriadTerm]], float]


def exact_curie_score(term: TriadTerm | None, reference_terms: list[TriadTerm]) -> float:
    """1.0 when *term*'s CURIE equals any reference CURIE, else 0.0.

    The default scorer for :func:`compare_outputs`. An ontology-aware scorer
    (one that credits ancestors and descendants) can be passed in its place.
    """
    curie_match, _ = matches(term, reference_terms)
    return 1.0 if curie_match else 0.0


@dataclass
class ArmDelta:
    """How a slot's suggestions moved between two runs, judged against a reference."""

    slot: str
    n_compared: int = 0
    changed: int = 0
    toward_reference: int = 0
    away_from_reference: int = 0
    transitions: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "n_compared": self.n_compared,
            "changed": self.changed,
            "toward_reference": self.toward_reference,
            "away_from_reference": self.away_from_reference,
            "transitions": dict(self.transitions.most_common()),
        }


def compare_outputs(
    baseline: LLMOutput,
    treatment: LLMOutput,
    reference: Reference,
    sample_ids: Iterable[str],
    scorer: Scorer = exact_curie_score,
) -> dict[str, ArmDelta]:
    """Compare two runs sample by sample and count where the treatment moved.

    ``changed`` counts samples whose suggested CURIE differs between runs.
    ``toward_reference`` counts changes where *scorer* rates the treatment's
    term above the baseline's against the reference; ``away_from_reference``
    the reverse. ``transitions`` records ``"baseline value -> treatment value"``
    for every change, so the direction of drift is visible and not just its size.
    """
    ids = list(sample_ids)
    base = triad_suggestions(baseline)
    treat = triad_suggestions(treatment)
    deltas = {slot: ArmDelta(slot=slot) for slot in ENV_TRIAD_SLOTS}
    for slot, delta in deltas.items():
        for sample_id in ids:
            before = base.get((sample_id, slot))
            after = treat.get((sample_id, slot))
            if before is None or after is None:
                continue
            delta.n_compared += 1
            before_term = suggested_term(before)
            after_term = suggested_term(after)
            before_curie = before_term.curie if before_term else None
            after_curie = after_term.curie if after_term else None
            if before_curie == after_curie:
                continue
            delta.changed += 1
            before_value = before_term.value if before_term else str(before.value)
            after_value = after_term.value if after_term else str(after.value)
            delta.transitions[f"{before_value} -> {after_value}"] += 1
            reference_terms = [t for t in reference.get(sample_id, {}).get(slot, []) if t.curie]
            if not reference_terms:
                continue
            before_score = scorer(before_term, reference_terms)
            after_score = scorer(after_term, reference_terms)
            if after_score > before_score:
                delta.toward_reference += 1
            elif before_score > after_score:
                delta.away_from_reference += 1
    return deltas
