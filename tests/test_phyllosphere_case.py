"""Tests for the phyllosphere test case: the snapshot, the submission object, the references."""

from nmdc_metadata_suggestor_ai_tool.evaluation import phyllosphere
from nmdc_metadata_suggestor_ai_tool.models.supplement import SupplementFile, SupplementKind


def test_snapshot_loads_with_192_biosamples_of_the_study() -> None:
    study = phyllosphere.load_study()
    samples = phyllosphere.load_biosamples()
    assert study["id"] == phyllosphere.STUDY_ID
    assert len(samples) == 192
    assert all(phyllosphere.STUDY_ID in s["associated_studies"] for s in samples)
    dois = {d["doi_value"] for d in study["associated_dois"]}
    assert dois == {f"doi:{phyllosphere.PUBLICATION_DOI}", f"doi:{phyllosphere.AWARD_DOI}"}


def test_submission_object_parses_to_both_dois_and_the_interface() -> None:
    from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import get_submission_fields

    parsed = get_submission_fields(phyllosphere.submission_object_for(phyllosphere.load_study()))
    assert parsed["mixs_extensions"] == ["PlantAssociatedInterface"]
    assert [d["value"] for d in parsed["dois"]] == [
        phyllosphere.PUBLICATION_DOI,
        phyllosphere.AWARD_DOI,
    ]
    assert parsed["study_name"].startswith("Seasonal activities")


def test_strip_triad_removes_only_the_three_slots_and_leaves_the_input_alone() -> None:
    samples = phyllosphere.load_biosamples()[:2]
    stripped = phyllosphere.strip_triad(samples)
    for original, copy in zip(samples, stripped, strict=True):
        assert not {"env_broad_scale", "env_local_scale", "env_medium"} & copy.keys()
        assert copy["id"] == original["id"]
        assert "env_medium" in original


def test_nmdc_reference_carries_the_stored_curies_as_is() -> None:
    reference = phyllosphere.nmdc_reference(phyllosphere.load_biosamples())
    assert len(reference) == 192
    first = next(iter(reference.values()))
    # Stored as written in NMDC, label/CURIE mismatch included; scoring is on the CURIE.
    assert first["env_broad_scale"][0].curie == "ENVO:01001442"


def supplement_table(rows: str) -> SupplementFile:
    header = "sample_name,env_broad_scale,env_local_scale,env_medium\n"
    return SupplementFile(
        filename=phyllosphere.SUPPLEMENT_REFERENCE_FILE,
        kind=SupplementKind.TABULAR,
        text=header + rows,
    )


def test_supplement_reference_joins_sample_name_to_biosample_id() -> None:
    samples = phyllosphere.load_biosamples()[:3]
    table = supplement_table(
        f"{samples[0]['name']},Terrestrial Biome [ENVO_00000446],Area of cropland [ENVO_01000892],"
        "agricultural soil [ENVO_00002259] | plant matter [ENVO_01001121]\n"
        f"{samples[1]['name']},Terrestrial Biome [ENVO_00000446],,\n"
        "NOT_A_SAMPLE,x [ENVO_00000001],,\n"
    )
    reference, unmatched = phyllosphere.supplement_reference([table], samples)
    assert set(reference) == {samples[0]["id"], samples[1]["id"]}
    assert unmatched == [samples[2]["id"]]
    medium = reference[samples[0]["id"]]["env_medium"]
    assert [t.curie for t in medium] == ["ENVO:00002259", "ENVO:01001121"]
    assert reference[samples[1]["id"]]["env_medium"] == []


def test_supplement_reference_requires_the_inlined_table() -> None:
    import pytest

    other = SupplementFile(filename="other.csv", kind=SupplementKind.TABULAR, text="a,b\n")
    with pytest.raises(ValueError, match=phyllosphere.SUPPLEMENT_REFERENCE_FILE):
        phyllosphere.supplement_reference([other], [])
