"""Tests for shared helpers in utils.utils."""

import pytest

from nmdc_metadata_suggestor_ai_tool.utils.utils import chunk_samples, iri_to_curie


@pytest.mark.parametrize(
    ("iri", "expected"),
    [
        ("http://purl.obolibrary.org/obo/ENVO_00001998", "ENVO:00001998"),
        ("http://purl.obolibrary.org/obo/UBERON_0001555", "UBERON:0001555"),
        # Only the first underscore separates prefix from local id.
        ("http://purl.obolibrary.org/obo/NCBITaxon_Union_0000030", "NCBITaxon:Union_0000030"),
    ],
)
def test_iri_to_curie_converts_obo_purls(iri: str, expected: str) -> None:
    assert iri_to_curie(iri) == expected


@pytest.mark.parametrize(
    "iri",
    [
        "https://example.com/ENVO_00001998",
        "http://www.w3.org/2002/07/owl#Thing",
        "",
    ],
)
def test_iri_to_curie_returns_none_for_non_obo_iris(iri: str) -> None:
    assert iri_to_curie(iri) is None


def test_chunk_samples_splits_evenly_and_keeps_the_remainder() -> None:
    samples = [{"id": str(i)} for i in range(5)]
    assert [len(c) for c in chunk_samples(samples, 2)] == [2, 2, 1]


def test_chunk_samples_of_an_empty_list_yields_nothing() -> None:
    assert list(chunk_samples([], 10)) == []
