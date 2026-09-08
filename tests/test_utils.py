"""Tests for shared helpers in utils.utils."""

from nmdc_metadata_suggestor_ai_tool.utils.utils import chunk_samples


def test_chunk_samples_splits_evenly_and_keeps_the_remainder() -> None:
    samples = [{"id": str(i)} for i in range(5)]
    assert [len(c) for c in chunk_samples(samples, 2)] == [2, 2, 1]


def test_chunk_samples_of_an_empty_list_yields_nothing() -> None:
    assert list(chunk_samples([], 10)) == []
