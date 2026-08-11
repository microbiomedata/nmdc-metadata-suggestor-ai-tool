"""Tests for the pinned ENVO index and env triad validation.

All offline: the index is a committed artifact, so nothing here touches the
network or the ENVO release.
"""

import pytest

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_ENV_TRIAD_VALUE_PATTERN,
    ENV_TRIAD_ANCHORS,
    ENV_TRIAD_SLOTS,
)
from nmdc_metadata_suggestor_ai_tool.envo_index import (
    CURATED_INTERFACES,
    EXPANSION_SEEDS,
    EnvoIndex,
    get_envo_index,
    get_slot_pattern,
    get_slot_valueset,
)
from nmdc_metadata_suggestor_ai_tool.schema_context import get_schema_view
from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import MixsExtensions

# CURIEs that nmdc-submission-schema still offers but ENVO has removed.
DEAD_CURIES = ["ENVO:02000139", "ENVO:02000145"]


@pytest.fixture(scope="module")
def index() -> EnvoIndex:
    return get_envo_index()


def enum_values(class_name: str, slot_name: str) -> list[str]:
    """Permissible values for one env triad slot on one interface, or []."""
    schema_view = get_schema_view()
    slot = {s.name: s for s in schema_view.class_induced_slots(class_name)}.get(slot_name)
    for item in (slot.any_of or []) if slot is not None else []:
        enum_def = schema_view.get_enum(item.range) if item.range else None
        if enum_def is not None:
            return [str(pv.text) for pv in enum_def.permissible_values.values()]
    return []


# --- index integrity --------------------------------------------------------


def test_index_loads_with_a_pinned_envo_version(index: EnvoIndex) -> None:
    assert index.envo_version
    assert "envo" in index.envo_version
    assert len(index.terms) > 4000


def test_index_holds_only_envo_terms(index: EnvoIndex) -> None:
    assert all(curie.startswith("ENVO:") for curie in index.terms)


def test_every_anchor_class_is_in_the_index(index: EnvoIndex) -> None:
    for slot, anchor in ENV_TRIAD_ANCHORS.items():
        assert index.get(anchor) is not None, f"{slot} anchor {anchor} missing"


def test_anchor_tags_agree_with_the_subclass_closure(index: EnvoIndex) -> None:
    """Anchors are precomputed at build time; the shipped DAG must reproduce them."""
    for slot, anchor in ENV_TRIAD_ANCHORS.items():
        tagged = {curie for curie, term in index.terms.items() if slot in term.anchors}
        assert tagged == index.descendants(anchor)


def test_biome_list_is_small_enough_to_inline(index: EnvoIndex) -> None:
    """The whole env_broad_scale universe must fit in a prompt for every extension."""
    biomes = index.biome_values()
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
    index: EnvoIndex, interface_name: str, slot: str, curie: str
) -> None:
    """Seeds must be looked up, not recalled: every one is checked against ENVO."""
    term = index.get(curie)
    assert term is not None, f"{interface_name}.{slot} seed {curie} is not in ENVO"
    assert not term.obsolete, f"{interface_name}.{slot} seed {curie} is obsolete"
    assert index.in_anchor(curie, slot), (
        f"{interface_name}.{slot} seed {curie} ({term.label}) is not under "
        f"{ENV_TRIAD_ANCHORS[slot]}"
    )


def test_seeds_are_only_declared_for_extensions_without_a_valueset() -> None:
    assert not CURATED_INTERFACES & EXPANSION_SEEDS.keys()


def test_no_seeds_are_declared_for_env_broad_scale() -> None:
    """env_broad_scale draws on the complete biome list, so seeds would be dead weight."""
    assert all("env_broad_scale" not in slots for slots in EXPANSION_SEEDS.values())


def test_generic_fallback_is_always_a_valid_value(index: EnvoIndex) -> None:
    for interface_name in MixsExtensions.__members__:
        for slot in ENV_TRIAD_SLOTS:
            fallback = index.generic_fallback(interface_name, slot)
            assert index.validate(fallback, slot, interface_name).ok, (
                f"{interface_name}.{slot} fallback {fallback!r} does not validate"
            )


def test_generic_fallback_prefers_the_curated_root(index: EnvoIndex) -> None:
    assert index.generic_fallback("SoilInterface", "env_medium") == "soil [ENVO:00001998]"
    assert index.generic_fallback("WaterInterface", "env_broad_scale") == (
        "aquatic biome [ENVO:00002030]"
    )


def test_generic_fallback_uses_a_seed_when_there_is_no_valueset(index: EnvoIndex) -> None:
    assert index.generic_fallback("AirInterface", "env_medium") == "air [ENVO:00002005]"
    assert index.generic_fallback("WastewaterSludgeInterface", "env_medium") == (
        "sludge [ENVO:00002044]"
    )


def test_flat_valuesets_have_no_broadest_member(index: EnvoIndex) -> None:
    """Local-scale value sets are sibling features, so no member may be crowned."""
    assert index.most_general_value(get_slot_valueset("SoilInterface", "env_local_scale")) is None
    assert index.most_general_value(get_slot_valueset("SoilInterface", "env_medium")) is not None


def test_local_scale_falls_back_to_the_bare_anchor(index: EnvoIndex) -> None:
    """A near-contentless fallback is the honest answer, and a nudge to use evidence."""
    for interface_name in MixsExtensions.__members__:
        assert index.generic_fallback(interface_name, "env_local_scale") in {
            "astronomical body part [ENVO:01000813]",
            "building [ENVO:00000073]",
        }


# --- search -----------------------------------------------------------------


def test_search_matches_whole_words_only(index: EnvoIndex) -> None:
    """'air' must not match 'dairy food product', which sits under the medium anchor."""
    labels = [term.label for term in index.search("air", slot="env_medium", limit=10)]
    assert "air" in labels
    assert not any("dairy" in label for label in labels)


def test_search_ranks_the_exact_label_first(index: EnvoIndex) -> None:
    hits = index.search("rhizosphere", slot="env_local_scale")
    assert hits[0].curie == "ENVO:00005801"


def test_search_respects_the_slot_anchor(index: EnvoIndex) -> None:
    """A biome must never surface as an env_medium candidate."""
    hits = index.search("marine", slot="env_medium", limit=20)
    assert all(index.in_anchor(term.curie, "env_medium") for term in hits)


def test_search_can_be_scoped_to_a_subtree(index: EnvoIndex) -> None:
    hits = index.search("activated", slot="env_medium", within=("ENVO:00002044",))
    assert [term.curie for term in hits] == ["ENVO:00002046"]


def test_search_skips_obsolete_terms_by_default(index: EnvoIndex) -> None:
    assert all(not term.obsolete for term in index.search("water", limit=50))


def test_search_falls_back_to_single_words_when_the_phrase_misses(index: EnvoIndex) -> None:
    """No ENVO term is labelled "rhizosphere soil", but both words are real."""
    labels = [term.label for term in index.search("rhizosphere soil", limit=5)]
    assert "rhizosphere" in labels
    assert "soil" in labels


def test_word_fallback_ranks_a_strong_match_over_a_synonym_match(index: EnvoIndex) -> None:
    """An exact label hit on the rarer word must beat a synonym hit on the common one."""
    labels = [term.label for term in index.search("rhizosphere soil", limit=2)]
    assert set(labels) == {"rhizosphere", "soil"}


def test_search_prefers_terms_matching_more_query_words(index: EnvoIndex) -> None:
    hits = index.search("forest floor litter", limit=1)
    assert hits[0].label == "forest floor"


def test_phrase_hits_still_win_outright(index: EnvoIndex) -> None:
    """The word fallback only runs when the whole phrase finds nothing."""
    assert index.search("drinking water", limit=1)[0].curie == "ENVO:00003064"


# --- hierarchy --------------------------------------------------------------


def test_descendants_follows_the_train_down(index: EnvoIndex) -> None:
    assert "ENVO:00002046" in index.descendants("ENVO:00002044")  # activated sludge < sludge


def test_is_under_includes_the_term_itself(index: EnvoIndex) -> None:
    anchor = ENV_TRIAD_ANCHORS["env_medium"]
    assert index.is_under(anchor, anchor)
    assert index.is_under("ENVO:00001998", anchor)  # soil
    assert not index.is_under(ENV_TRIAD_ANCHORS["env_broad_scale"], anchor)


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


def test_validate_accepts_a_curated_value(index: EnvoIndex) -> None:
    assert index.validate("soil [ENVO:00001998]", "env_medium", "SoilInterface").ok


@pytest.mark.parametrize("curie", DEAD_CURIES)
def test_validate_rejects_curies_envo_has_removed(index: EnvoIndex, curie: str) -> None:
    result = index.validate(f"whatever [{curie}]", "env_medium")
    assert not result.ok
    assert "not in ENVO" in result.failures[0]


def test_validate_rejects_a_prefix_the_slot_does_not_allow(index: EnvoIndex) -> None:
    result = index.validate("gut [UBERON:0001555]", "env_medium", "SoilInterface")
    assert not result.ok
    assert "pattern" in result.failures[0]


def test_validate_accepts_uberon_where_the_slot_allows_it(index: EnvoIndex) -> None:
    """HostAssociatedInterface's medium and local scale admit UBERON by schema pattern."""
    result = index.validate("gut [UBERON:0001555]", "env_medium", "HostAssociatedInterface")
    assert result.ok
    assert "cannot be checked against the pinned index" in result.warnings[0]


def test_validate_rejects_a_biome_proposed_as_a_medium(index: EnvoIndex) -> None:
    result = index.validate("temperate grassland biome [ENVO:01000193]", "env_medium")
    assert not result.ok
    assert "does not descend from" in result.failures[0]


def test_validate_catches_label_drift_and_offers_the_fix(index: EnvoIndex) -> None:
    result = index.validate("dirt [ENVO:00001998]", "env_medium")
    assert not result.ok
    assert result.corrected_value == "soil [ENVO:00001998]"


def test_validate_rejects_a_non_triad_slot(index: EnvoIndex) -> None:
    assert not index.validate("soil [ENVO:00001998]", "depth").ok


def test_validate_accepts_plant_ontology_where_the_slot_allows_it(index: EnvoIndex) -> None:
    """26 of PlantAssociatedInterface's 28 curated medium values are PO terms."""
    result = index.validate("root [PO:0009005]", "env_medium", "PlantAssociatedInterface")
    assert result.ok
    assert result.warnings
    assert not index.validate("root [PO:0009005]", "env_medium", "AirInterface").ok


@pytest.mark.parametrize("class_name", sorted(CURATED_INTERFACES))
@pytest.mark.parametrize("slot_name", ENV_TRIAD_SLOTS)
def test_validation_never_rejects_a_curated_value(
    index: EnvoIndex, class_name: str, slot_name: str
) -> None:
    """The gate is calibrated so only the genuinely dead CURIEs fail."""
    rejected = [
        value
        for value in enum_values(class_name, slot_name)
        if not index.validate(value, slot_name, class_name).ok
    ]
    assert all(any(curie in value for curie in DEAD_CURIES) for value in rejected), rejected


def test_anchor_gate_does_not_second_guess_a_curated_value(index: EnvoIndex) -> None:
    """rhizosphere is a curated plant medium that ENVO calls a system, not a material."""
    value = "rhizosphere [ENVO:00005801]"
    assert index.validate(value, "env_medium", "PlantAssociatedInterface").ok
    assert not index.validate(value, "env_medium", "AirInterface").ok


def test_dead_curies_fail_even_inside_a_curated_valueset(index: EnvoIndex) -> None:
    """Membership excuses the anchor gate, never a term ENVO has removed."""
    result = index.validate("subterranean lake [ENVO:02000145]", "env_medium", "WaterInterface")
    assert result.in_valueset
    assert not result.ok


def test_valueset_membership_drives_the_source_label(index: EnvoIndex) -> None:
    curated = index.validate("soil [ENVO:00001998]", "env_medium", "SoilInterface")
    assert curated.in_valueset and curated.source == "submission_enum"

    expanded = index.validate("activated sludge [ENVO:00002046]", "env_medium", "AirInterface")
    assert not expanded.in_valueset and expanded.source == "envo_expansion"


def test_valueset_is_empty_for_extensions_without_one() -> None:
    assert get_slot_valueset("SoilInterface", "env_medium")
    assert not get_slot_valueset("AirInterface", "env_medium")
    assert not get_slot_valueset(None, "env_medium")
    assert not get_slot_valueset("NotAnInterface", "env_medium")


def test_local_scale_anchor_is_advisory_not_a_gate(index: EnvoIndex) -> None:
    """Curated local-scale values outside the anchor must pass with a warning."""
    for value in ["aquifer [ENVO:00012408]", "farm [ENVO:00000078]", "fen [ENVO:00000232]"]:
        result = index.validate(value, "env_local_scale", "SoilInterface")
        assert result.ok, value
        assert result.warnings, value
        assert not index.validate(value, "env_medium", "SoilInterface").ok, value
