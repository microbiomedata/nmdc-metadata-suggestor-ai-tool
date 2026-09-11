"""Unit tests for env triad scoring: reference parsing, matching, and arm comparison."""

from nmdc_metadata_suggestor_ai_tool.evaluation.env_triad_scoring import (
    TriadTerm,
    compare_outputs,
    coverage,
    exact_curie_score,
    matches,
    parse_triad_value,
    reference_from_biosamples,
    reference_from_rows,
    rekey_reference,
)
from nmdc_metadata_suggestor_ai_tool.models.llm_output import (
    LLMOutput,
    MetadataFieldSuggestion,
    TriadProvenance,
)


def test_parse_normalizes_underscored_curie_and_keeps_label_case() -> None:
    assert parse_triad_value("Terrestrial Biome [ENVO_00000446]") == [
        TriadTerm(label="Terrestrial Biome", curie="ENVO:00000446")
    ]


def test_parse_splits_pipe_separated_terms() -> None:
    terms = parse_triad_value("agricultural soil [ENVO_00002259] | plant matter [ENVO_01001121]")
    assert [t.curie for t in terms] == ["ENVO:00002259", "ENVO:01001121"]
    assert [t.label for t in terms] == ["agricultural soil", "plant matter"]


def test_parse_colon_form_and_value_roundtrip() -> None:
    (term,) = parse_triad_value("soil [ENVO:00001998]")
    assert term.value == "soil [ENVO:00001998]"


def test_parse_keeps_unbracketed_text_as_label_without_curie() -> None:
    assert parse_triad_value("leaf surface") == [TriadTerm(label="leaf surface", curie="")]


def test_parse_ignores_empty_and_na_cells() -> None:
    assert parse_triad_value("") == []
    assert parse_triad_value(None) == []
    assert parse_triad_value("NA") == []
    assert parse_triad_value(" | ") == []


def test_reference_from_rows_keys_on_sample_name() -> None:
    rows = [
        {
            "sample_name": "G5R1_MAIN_09MAY2016",
            "env_broad_scale": "Terrestrial Biome [ENVO_00000446]",
            "env_local_scale": "Area of cropland [ENVO_01000892]",
            "env_medium": "agricultural soil [ENVO_00002259] | plant matter [ENVO_01001121]",
        },
        {"sample_name": "", "env_broad_scale": "ignored [ENVO_00000000]"},
    ]
    ref = reference_from_rows(rows)
    assert list(ref) == ["G5R1_MAIN_09MAY2016"]
    assert ref["G5R1_MAIN_09MAY2016"]["env_broad_scale"][0].curie == "ENVO:00000446"
    assert len(ref["G5R1_MAIN_09MAY2016"]["env_medium"]) == 2


def test_reference_from_biosamples_reads_raw_value_then_term() -> None:
    records: list[dict] = [
        {
            "id": "nmdc:bsm-1",
            "env_broad_scale": {"has_raw_value": "agricultural biome [ENVO:01001442]"},
            "env_local_scale": {"term": {"id": "ENVO:01000892", "name": "area of cropland"}},
        },
        {"name": "no id, skipped"},
    ]
    ref = reference_from_biosamples(records)
    assert list(ref) == ["nmdc:bsm-1"]
    assert ref["nmdc:bsm-1"]["env_broad_scale"][0].curie == "ENVO:01001442"
    assert ref["nmdc:bsm-1"]["env_local_scale"][0].value == "area of cropland [ENVO:01000892]"
    assert ref["nmdc:bsm-1"]["env_medium"] == []


def test_rekey_reference_drops_unmapped_rows() -> None:
    ref = reference_from_rows([{"sample_name": "A", "env_medium": "x [ENVO:1]"}])
    assert rekey_reference(ref, {"A": "nmdc:bsm-a"}) == {"nmdc:bsm-a": ref["A"]}
    assert rekey_reference(ref, {"B": "nmdc:bsm-b"}) == {}


def suggestion(
    sample_id: str, slot: str, value: str, tier: str = "submission_enum"
) -> MetadataFieldSuggestion:
    return MetadataFieldSuggestion(
        id=sample_id,
        field_name=slot,
        reason="test",
        value=value,
        provenance=TriadProvenance(tier=tier, outcome="accepted"),  # type: ignore[arg-type]
    )


def test_coverage_counts_answered_unlabeled_and_unknown_ids() -> None:
    output = LLMOutput(
        metadata_fields=[
            suggestion("a", "env_medium", "leaf [PO:0025034]"),
            suggestion("a", "env_broad_scale", "terrestrial biome [ENVO:00000446]"),
            suggestion("", "env_medium", "leaf [PO:0025034]"),
            suggestion("ghost", "env_medium", "leaf [PO:0025034]"),
        ]
    )
    assert coverage(output, ["a", "b"]) == {
        "n_samples": 2,
        "samples_with_suggestions": 1,
        "suggestions_total": 4,
        "suggestions_without_id": 1,
        "suggestions_with_unknown_id": 1,
    }


def test_compare_outputs_records_direction_of_change() -> None:
    reference = reference_from_rows(
        [{"sample_name": s, "env_medium": "plant matter [ENVO_01001121]"} for s in ("a", "b", "c")]
    )
    baseline = LLMOutput(
        metadata_fields=[
            suggestion("a", "env_medium", "leaf [PO:0025034]"),
            suggestion("b", "env_medium", "plant matter [ENVO:01001121]"),
            suggestion("c", "env_medium", "leaf [PO:0025034]"),
        ]
    )
    treatment = LLMOutput(
        metadata_fields=[
            suggestion("a", "env_medium", "plant matter [ENVO:01001121]"),  # toward
            suggestion("b", "env_medium", "leaf [PO:0025034]"),  # away
            suggestion("c", "env_medium", "leaf [PO:0025034]"),  # unchanged
        ]
    )
    delta = compare_outputs(baseline, treatment, reference, ["a", "b", "c"])["env_medium"]
    assert delta.n_compared == 3
    assert delta.changed == 2
    assert delta.toward_reference == 1
    assert delta.away_from_reference == 1
    assert delta.transitions == {
        "leaf [PO:0025034] -> plant matter [ENVO:01001121]": 1,
        "plant matter [ENVO:01001121] -> leaf [PO:0025034]": 1,
    }


def test_compare_outputs_skips_samples_missing_from_either_arm() -> None:
    baseline = LLMOutput(metadata_fields=[suggestion("a", "env_medium", "leaf [PO:0025034]")])
    treatment = LLMOutput(metadata_fields=[])
    delta = compare_outputs(baseline, treatment, {}, ["a"])["env_medium"]
    assert delta.n_compared == 0
    assert delta.changed == 0


def test_matches_is_case_insensitive_on_labels_and_exact_on_curies() -> None:
    reference = parse_triad_value("Terrestrial Biome [ENVO_00000446]")
    assert matches(TriadTerm("terrestrial biome", "ENVO:00000446"), reference) == (True, True)
    assert matches(TriadTerm("terrestrial biome", "ENVO:00000447"), reference) == (False, True)
    assert matches(None, reference) == (False, False)
    assert exact_curie_score(TriadTerm("x", "ENVO:00000446"), reference) == 1.0


def test_compare_outputs_accepts_a_graded_scorer() -> None:
    """A scorer that credits partial agreement decides direction, not exact match."""
    reference = reference_from_rows([{"sample_name": "a", "env_medium": "soil [ENVO:00001998]"}])
    baseline = LLMOutput(metadata_fields=[suggestion("a", "env_medium", "water [ENVO:00002006]")])
    treatment = LLMOutput(
        metadata_fields=[suggestion("a", "env_medium", "agricultural soil [ENVO:00002259]")]
    )
    graded = {"ENVO:00002006": 0.0, "ENVO:00002259": 0.9}

    def scorer(term: TriadTerm | None, _reference: list[TriadTerm]) -> float:
        return graded[term.curie] if term else 0.0

    exact = compare_outputs(baseline, treatment, reference, ["a"])["env_medium"]
    assert (exact.changed, exact.toward_reference, exact.away_from_reference) == (1, 0, 0)
    ontology = compare_outputs(baseline, treatment, reference, ["a"], scorer=scorer)["env_medium"]
    assert (ontology.changed, ontology.toward_reference, ontology.away_from_reference) == (1, 1, 0)
