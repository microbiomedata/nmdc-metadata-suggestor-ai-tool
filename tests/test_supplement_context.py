"""Tests for turning a supplement result into LLM context messages."""

from nmdc_metadata_suggestor_ai_tool.models.supplement import (
    SupplementFile,
    SupplementKind,
    SupplementRetrievalResult,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements import (
    describe_supplement,
    format_supplement_context,
)


def csv_file() -> SupplementFile:
    return SupplementFile(
        filename="MOESM4_ESM.csv",
        kind=SupplementKind.TABULAR,
        source="europepmc",
        caption="Supplementary Data 1",
        text="sample_name,env_medium\nG5R1,plant matter [ENVO_01001121]\n",
    )


def pdf_file() -> SupplementFile:
    return SupplementFile(
        filename="MOESM1_ESM.pdf",
        kind=SupplementKind.DOCUMENT,
        source="europepmc",
        saved_path="/tmp/MOESM1_ESM.pdf",
    )


def test_describe_supplement_names_source_kind_and_caption() -> None:
    assert (
        describe_supplement(csv_file())
        == "Supplementary file MOESM4_ESM.csv [europepmc, tabular]: Supplementary Data 1"
    )
    assert (
        describe_supplement(pdf_file()) == "Supplementary file MOESM1_ESM.pdf [europepmc, document]"
    )


def test_format_supplement_context_inlines_text_and_inventories_the_rest() -> None:
    result = SupplementRetrievalResult(doi="10.1/x", files=[pdf_file(), csv_file()])
    messages = format_supplement_context(result)
    assert len(messages) == 2
    inventory, table = messages
    assert inventory.startswith("Supplementary materials retrieved for DOI 10.1/x:")
    assert "MOESM1_ESM.pdf [europepmc, document] (retrieved but not shown)" in inventory
    assert "MOESM4_ESM.csv [europepmc, tabular]: Supplementary Data 1 (included below)" in inventory
    assert table.startswith("Supplementary file MOESM4_ESM.csv")
    assert table.endswith(csv_file().text or "")


def test_format_supplement_context_is_empty_without_inlined_text() -> None:
    assert format_supplement_context(SupplementRetrievalResult(doi="10.1/x")) == []
    assert (
        format_supplement_context(SupplementRetrievalResult(doi="10.1/x", files=[pdf_file()])) == []
    )


def test_format_supplement_context_truncates_each_file() -> None:
    result = SupplementRetrievalResult(doi="10.1/x", files=[csv_file()])
    _, table = format_supplement_context(result, max_chars=10)
    body = table.split("\n", 1)[1]
    assert body == "sample_nam\n[truncated]"
