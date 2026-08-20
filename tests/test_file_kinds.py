"""Tests for the reusable file-type taxonomy in ``file_kinds``."""

from nmdc_metadata_suggestor_ai_tool.file_kinds import (
    DEFAULT_USEFUL_KINDS,
    EXTENSION_KINDS,
    TEXT_LIKE_EXTENSIONS,
    classify_file,
    file_extension,
)
from nmdc_metadata_suggestor_ai_tool.models.supplement import SupplementKind


def test_file_extension() -> None:
    assert file_extension("data.CSV") == "csv"
    assert file_extension("/path/to/Table_S1.xlsx") == "xlsx"
    assert file_extension("archive.tar.gz") == "gz"
    assert file_extension("README") == ""


def test_classify_file() -> None:
    assert classify_file("samples.tsv") == SupplementKind.TABULAR
    assert classify_file("methods.docx") == SupplementKind.DOCUMENT
    assert classify_file("gel.tiff") == SupplementKind.IMAGE
    assert classify_file("clip.mp4") == SupplementKind.MEDIA
    assert classify_file("reads.bam") == SupplementKind.SEQUENCE
    assert classify_file("bundle.zip") == SupplementKind.ARCHIVE
    assert classify_file("notes") == SupplementKind.OTHER


def test_taxonomy_constants_are_consistent() -> None:
    # Every text-like extension is a known DOCUMENT/TABULAR extension.
    for ext in TEXT_LIKE_EXTENSIONS:
        assert ext in EXTENSION_KINDS
    assert DEFAULT_USEFUL_KINDS == frozenset({SupplementKind.TABULAR, SupplementKind.DOCUMENT})


def test_classify_supplement_alias_still_works() -> None:
    # The supplements package re-exports classify_file under its documented name.
    from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements import (
        classify_supplement,
    )

    assert classify_supplement("x.csv") == SupplementKind.TABULAR
    assert classify_supplement is classify_file
