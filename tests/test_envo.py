"""Tests for ENVO lookups and env triad validation.

Ontology access is oaklib's job; these cover the NMDC layer on top of it --
anchor classes per slot, expansion seeds, the resolution tiers, and the gate.
oaklib caches the ontology after first use, so only a cold environment pays a
download.
"""

import re

import pytest

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_ENV_TRIAD_VALUE_PATTERN,
    ENV_TRIAD_ANCHORS,
    ENV_TRIAD_SLOTS,
)
from nmdc_metadata_suggestor_ai_tool.envo import (
    CURATED_INTERFACES,
    EXPANSION_SEEDS,
    Envo,
    get_envo,
    get_slot_pattern,
    get_slot_valueset,
)
from nmdc_metadata_suggestor_ai_tool.schema_context import get_schema_view
from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import MixsExtensions

# CURIEs that nmdc-submission-schema still offers but ENVO has removed.
DEAD_CURIES = ["ENVO:02000139", "ENVO:02000145"]


@pytest.fixture(scope="module")
def envo() -> Envo:
    return get_envo()


def enum_values(class_name: str, slot_name: str) -> list[str]:
    """Permissible values for one env triad slot on one interface, or []."""
    schema_view = get_schema_view()
    slot = {s.name: s for s in schema_view.class_induced_slots(class_name)}.get(slot_name)
    for item in (slot.any_of or []) if slot is not None else []:
        enum_def = schema_view.get_enum(item.range) if item.range else None
        if enum_def is not None:
            return [str(pv.text) for pv in enum_def.permissible_values.values()]
    return []


# --- adapter wiring --------------------------------------------------------


def test_adapter_reports_the_envo_release_it_is_serving(envo: Envo) -> None:
    """Provenance: failure messages quote this, so it must resolve."""
    assert envo.envo_version
    assert "envo" in envo.envo_version


def test_candidate_pools_hold_only_envo_terms(envo: Envo) -> None:
    """The adapter carries imported classes (FOODON, CHEBI); slots take ENVO only."""
    for slot in ENV_TRIAD_SLOTS:
        pool = envo.values_under(ENV_TRIAD_ANCHORS[slot], limit=200)
        assert all("[ENVO:" in value for value in pool), slot


def test_every_anchor_class_is_in_the_index(envo: Envo) -> None:
    for slot, anchor in ENV_TRIAD_ANCHORS.items():
        assert envo.get(anchor) is not None, f"{slot} anchor {anchor} missing"


def test_anchor_membership_agrees_with_the_subclass_closure(envo: Envo) -> None:
    """in_anchor is answered from a cached closure; it must match a live walk."""
    for slot, anchor in ENV_TRIAD_ANCHORS.items():
        walked = {c for c in envo.descendants(anchor) if c.startswith("ENVO:")}
        assert all(envo.in_anchor(c, slot) for c in walked), slot
        assert envo.in_anchor(anchor, slot), f"{slot} anchor should satisfy itself"


def test_biome_list_is_small_enough_to_inline(envo: Envo) -> None:
    """The whole env_broad_scale universe must fit in a prompt for every extension."""
    biomes = envo.biome_values()
    assert 100 < len(biomes) < 300
    assert len(", ".join(biomes)) < 20_000


# --- expansion seeds --------------------------------------------------------


@pytest.mark.parametrize(
    ("interface_name", "slot", "curie"),
    [
        (interface_name, slot, curie)
        for interface_name, slots in EXPANSION_SEEDS.items()
        for slot, curies in slots.items()
        for curie in curies
    ],
)
def test_expansion_seed_is_current_and_anchored(
    envo: Envo, interface_name: str, slot: str, curie: str
) -> None:
    """Seeds must be looked up, not recalled: every one is checked against ENVO."""
    term = envo.get(curie)
    assert term is not None, f"{interface_name}.{slot} seed {curie} is not in ENVO"
    assert not term.obsolete, f"{interface_name}.{slot} seed {curie} is obsolete"
    assert envo.in_anchor(curie, slot), (
        f"{interface_name}.{slot} seed {curie} ({term.label}) is not under "
        f"{ENV_TRIAD_ANCHORS[slot]}"
    )


def test_seeds_are_only_declared_for_extensions_without_a_valueset() -> None:
    assert not CURATED_INTERFACES & EXPANSION_SEEDS.keys()


def test_no_seeds_are_declared_for_env_broad_scale() -> None:
    """env_broad_scale draws on the complete biome list, so seeds would be dead weight."""
    assert all("env_broad_scale" not in slots for slots in EXPANSION_SEEDS.values())


def test_generic_fallback_is_always_a_valid_value(envo: Envo) -> None:
    for interface_name in MixsExtensions.__members__:
        for slot in ENV_TRIAD_SLOTS:
            fallback = envo.generic_fallback(interface_name, slot)
            assert envo.validate(fallback, slot, interface_name).ok, (
                f"{interface_name}.{slot} fallback {fallback!r} does not validate"
            )


def test_generic_fallback_prefers_the_curated_root(envo: Envo) -> None:
    assert envo.generic_fallback("SoilInterface", "env_medium") == "soil [ENVO:00001998]"
    assert envo.generic_fallback("WaterInterface", "env_broad_scale") == (
        "aquatic biome [ENVO:00002030]"
    )


def test_generic_fallback_uses_a_seed_when_there_is_no_valueset(envo: Envo) -> None:
    assert envo.generic_fallback("AirInterface", "env_medium") == "air [ENVO:00002005]"
    assert envo.generic_fallback("WastewaterSludgeInterface", "env_medium") == (
        "sludge [ENVO:00002044]"
    )


def test_flat_valuesets_have_no_broadest_member(envo: Envo) -> None:
    """Local-scale value sets are sibling features, so no member may be crowned."""
    assert envo.most_general_value(get_slot_valueset("SoilInterface", "env_local_scale")) is None
    assert envo.most_general_value(get_slot_valueset("SoilInterface", "env_medium")) is not None


def test_local_scale_falls_back_to_the_bare_anchor(envo: Envo) -> None:
    """A near-contentless fallback is the honest answer, and a nudge to use evidence."""
    for interface_name in MixsExtensions.__members__:
        assert envo.generic_fallback(interface_name, "env_local_scale") in {
            "astronomical body part [ENVO:01000813]",
            "building [ENVO:00000073]",
        }


# --- search -----------------------------------------------------------------


def test_search_matches_whole_words_only(envo: Envo) -> None:
    """'air' must not match 'dairy food product', which sits under the medium anchor."""
    labels = [term.label for term in envo.search("air", slot="env_medium", limit=10)]
    assert "air" in labels
    assert not any("dairy" in label for label in labels)


def test_search_ranks_the_exact_label_first(envo: Envo) -> None:
    hits = envo.search("rhizosphere", slot="env_local_scale")
    assert hits[0].curie == "ENVO:00005801"


def test_search_respects_the_slot_anchor(envo: Envo) -> None:
    """A biome must never surface as an env_medium candidate."""
    hits = envo.search("marine", slot="env_medium", limit=20)
    assert all(envo.in_anchor(term.curie, "env_medium") for term in hits)


def test_search_can_be_scoped_to_a_subtree(envo: Envo) -> None:
    hits = envo.search("activated", slot="env_medium", within=("ENVO:00002044",))
    assert [term.curie for term in hits] == ["ENVO:00002046"]


def test_search_skips_obsolete_terms_by_default(envo: Envo) -> None:
    assert all(not term.obsolete for term in envo.search("water", limit=50))


def test_search_falls_back_to_single_words_when_the_phrase_misses(envo: Envo) -> None:
    """No ENVO term is labelled "rhizosphere soil", but both words are real."""
    labels = [term.label for term in envo.search("rhizosphere soil", limit=5)]
    assert "rhizosphere" in labels
    assert "soil" in labels


def test_word_fallback_ranks_a_strong_match_over_a_synonym_match(envo: Envo) -> None:
    """An exact label hit on the rarer word must beat a synonym hit on the common one."""
    labels = [term.label for term in envo.search("rhizosphere soil", limit=2)]
    assert set(labels) == {"rhizosphere", "soil"}


def test_search_prefers_terms_matching_more_query_words(envo: Envo) -> None:
    hits = envo.search("forest floor litter", limit=1)
    assert hits[0].label == "forest floor"


def test_phrase_hits_still_win_outright(envo: Envo) -> None:
    """The word fallback only runs when the whole phrase finds nothing."""
    assert envo.search("drinking water", limit=1)[0].curie == "ENVO:00003064"


# --- hierarchy --------------------------------------------------------------


def test_descendants_follows_the_train_down(envo: Envo) -> None:
    assert "ENVO:00002046" in envo.descendants("ENVO:00002044")  # activated sludge < sludge


def test_is_under_includes_the_term_itself(envo: Envo) -> None:
    anchor = ENV_TRIAD_ANCHORS["env_medium"]
    assert envo.is_under(anchor, anchor)
    assert envo.is_under("ENVO:00001998", anchor)  # soil
    assert not envo.is_under(ENV_TRIAD_ANCHORS["env_broad_scale"], anchor)


# --- validation -------------------------------------------------------------


def test_slot_pattern_comes_from_the_schema() -> None:
    """Allowed prefixes vary by interface; validation must read the real pattern."""
    schema_view = get_schema_view()
    for class_name in sorted(MixsExtensions.__members__):
        slots = {s.name: s for s in schema_view.class_induced_slots(class_name)}
        for slot_name in ENV_TRIAD_SLOTS:
            assert get_slot_pattern(class_name, slot_name).pattern == slots[slot_name].pattern


def test_slot_pattern_falls_back_to_envo_only() -> None:
    """The default is the strictest of the three, so it never over-accepts."""
    assert get_slot_pattern(None, "env_medium").pattern == DEFAULT_ENV_TRIAD_VALUE_PATTERN
    assert get_slot_pattern("NotAnInterface", "env_medium").pattern == (
        DEFAULT_ENV_TRIAD_VALUE_PATTERN
    )


def test_validate_accepts_a_curated_value(envo: Envo) -> None:
    assert envo.validate("soil [ENVO:00001998]", "env_medium", "SoilInterface").ok


@pytest.mark.parametrize("curie", DEAD_CURIES)
def test_validate_rejects_curies_envo_has_removed(envo: Envo, curie: str) -> None:
    result = envo.validate(f"whatever [{curie}]", "env_medium")
    assert not result.ok
    assert "not in ENVO" in result.failures[0]


def test_obsolete_terms_redirect_to_their_replacement(envo: Envo) -> None:
    """oaklib carries `term replaced by`, so an obsolete term can be redirected."""
    result = envo.validate(
        "obsolete cultivated habitat [ENVO:00000113]", "env_local_scale", "SoilInterface"
    )
    assert not result.ok
    assert "obsolete" in result.failures[0]
    assert result.corrected_value == "cultivated environment [ENVO:01000311]"


def test_validate_rejects_a_prefix_the_slot_does_not_allow(envo: Envo) -> None:
    result = envo.validate("gut [UBERON:0001555]", "env_medium", "SoilInterface")
    assert not result.ok
    assert "pattern" in result.failures[0]


def test_validate_accepts_uberon_where_the_slot_allows_it(envo: Envo) -> None:
    """HostAssociatedInterface's medium and local scale admit UBERON by schema pattern."""
    result = envo.validate("gut [UBERON:0001555]", "env_medium", "HostAssociatedInterface")
    assert result.ok
    assert "cannot be checked against the ontology" in result.warnings[0]


def test_validate_rejects_a_biome_proposed_as_a_medium(envo: Envo) -> None:
    result = envo.validate("temperate grassland biome [ENVO:01000193]", "env_medium")
    assert not result.ok
    assert "does not descend from" in result.failures[0]


def test_validate_catches_label_drift_and_offers_the_fix(envo: Envo) -> None:
    result = envo.validate("dirt [ENVO:00001998]", "env_medium")
    assert not result.ok
    assert result.corrected_value == "soil [ENVO:00001998]"


# --- composed values: label and CURIE naming different terms ------------------
#
# A model that assembles "label [CURIE]" rather than copying an entry can pair a label from
# one value-set entry with a CURIE from another. Both halves then resolve, so the CURIE alone
# cannot say which was meant.


def test_conflicting_label_and_curie_repairs_toward_the_label(envo: Envo) -> None:
    """Observed in the wild: the label's own CURIE is the one the reason text argues for."""
    result = envo.validate(
        "temperate coniferous forest biome [ENVO:01000219]", "env_broad_scale", "SoilInterface"
    )
    assert not result.ok
    assert result.corrected_value == "temperate coniferous forest biome [ENVO:01000211]"


def test_the_two_halves_of_that_value_are_separate_valueset_entries(envo: Envo) -> None:
    """Neither half is invented -- which is exactly why the CURIE cannot arbitrate."""
    curated = get_slot_valueset("SoilInterface", "env_broad_scale")
    assert "temperate coniferous forest biome [ENVO:01000211]" in curated
    assert "anthropogenic terrestrial biome [ENVO:01000219]" in curated


def test_a_label_naming_nothing_is_treated_as_drift(envo: Envo) -> None:
    """'dirt' is no ENVO term, so the CURIE is sound and only the label needs fixing."""
    result = envo.validate("dirt [ENVO:00001998]", "env_medium", "SoilInterface")
    assert result.corrected_value == "soil [ENVO:00001998]"


def test_a_synonym_label_normalises_to_the_official_one(envo: Envo) -> None:
    result = envo.validate("regolith [ENVO:00001998]", "env_medium", "SoilInterface")
    assert result.corrected_value == "soil [ENVO:00001998]"
    assert "synonym" in result.failures[0]


def test_no_repair_when_the_label_names_a_term_that_fails_the_slot(envo: Envo) -> None:
    """soil is a real term, but not a biome -- so neither half of the value is usable."""
    result = envo.validate("soil [ENVO:01000219]", "env_broad_scale", "SoilInterface")
    assert not result.ok
    assert result.corrected_value is None


def test_every_curated_value_is_internally_consistent(envo: Envo) -> None:
    """The value sets are not the source of composed values; the model is."""
    mismatched = []
    for interface_name in sorted(CURATED_INTERFACES):
        for slot in ENV_TRIAD_SLOTS:
            for value in get_slot_valueset(interface_name, slot):
                match = re.match(r"^(.*) \[(ENVO:\d+)\]$", value)
                if not match:
                    continue
                term = envo.get(match.group(2))
                if term and term.label != match.group(1):
                    mismatched.append(value)
    assert not mismatched


def test_validate_rejects_a_non_triad_slot(envo: Envo) -> None:
    assert not envo.validate("soil [ENVO:00001998]", "depth").ok


def test_validate_accepts_plant_ontology_where_the_slot_allows_it(envo: Envo) -> None:
    """26 of PlantAssociatedInterface's 28 curated medium values are PO terms."""
    result = envo.validate("root [PO:0009005]", "env_medium", "PlantAssociatedInterface")
    assert result.ok
    assert result.warnings
    assert not envo.validate("root [PO:0009005]", "env_medium", "AirInterface").ok


@pytest.mark.parametrize("class_name", sorted(CURATED_INTERFACES))
@pytest.mark.parametrize("slot_name", ENV_TRIAD_SLOTS)
def test_validation_never_rejects_a_curated_value(
    envo: Envo, class_name: str, slot_name: str
) -> None:
    """The gate is calibrated so only the genuinely dead CURIEs fail."""
    rejected = [
        value
        for value in enum_values(class_name, slot_name)
        if not envo.validate(value, slot_name, class_name).ok
    ]
    assert all(any(curie in value for curie in DEAD_CURIES) for value in rejected), rejected


def test_anchor_gate_does_not_second_guess_a_curated_value(envo: Envo) -> None:
    """rhizosphere is a curated plant medium that ENVO calls a system, not a material."""
    value = "rhizosphere [ENVO:00005801]"
    assert envo.validate(value, "env_medium", "PlantAssociatedInterface").ok
    assert not envo.validate(value, "env_medium", "AirInterface").ok


def test_dead_curies_fail_even_inside_a_curated_valueset(envo: Envo) -> None:
    """Membership excuses the anchor gate, never a term ENVO has removed."""
    result = envo.validate("subterranean lake [ENVO:02000145]", "env_medium", "WaterInterface")
    assert result.in_valueset
    assert not result.ok


def test_valueset_membership_drives_the_source_label(envo: Envo) -> None:
    curated = envo.validate("soil [ENVO:00001998]", "env_medium", "SoilInterface")
    assert curated.in_valueset and curated.source == "submission_enum"

    expanded = envo.validate("activated sludge [ENVO:00002046]", "env_medium", "AirInterface")
    assert not expanded.in_valueset and expanded.source == "envo_expansion"


def test_valueset_is_empty_for_extensions_without_one() -> None:
    assert get_slot_valueset("SoilInterface", "env_medium")
    assert not get_slot_valueset("AirInterface", "env_medium")
    assert not get_slot_valueset(None, "env_medium")
    assert not get_slot_valueset("NotAnInterface", "env_medium")


def test_local_scale_anchor_is_advisory_not_a_gate(envo: Envo) -> None:
    """Curated local-scale values outside the anchor must pass with a warning."""
    for value in ["aquifer [ENVO:00012408]", "farm [ENVO:00000078]", "fen [ENVO:00000232]"]:
        result = envo.validate(value, "env_local_scale", "SoilInterface")
        assert result.ok, value
        assert result.warnings, value
        assert not envo.validate(value, "env_medium", "SoilInterface").ok, value


def test_a_piped_multi_term_value_fails_with_a_clear_reason(envo: Envo) -> None:
    """ENVO documents the pipe form and the schema regex tolerates it; we do not support it."""
    result = envo.validate(
        "soil [ENVO:00001998]|sediment [ENVO:00002007]", "env_medium", "SoilInterface"
    )
    assert not result.ok
    assert "one" in result.failures[0] and "|" in result.failures[0]
