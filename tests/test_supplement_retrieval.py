"""Tests for supplementary-material retrieval via Europe PMC."""

import io
import os
import zipfile

import responses

from nmdc_metadata_suggestor_ai_tool.constants import (
    EUROPEPMC_API_URL,
    EUROPEPMC_SUPPL_URL_TEMPLATE,
)
from nmdc_metadata_suggestor_ai_tool.models.supplement import SupplementKind
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import remove_temp_files
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.retrieve_supplements import (
    classify_supplement,
    find_supplement_source_europepmc,
    retrieve_supplements,
)

DOI = "10.1038/s41564-020-00861-0"
SUPPL_URL = EUROPEPMC_SUPPL_URL_TEMPLATE.format(source="PMC", article_id="PMC123456")


def _make_zip(members: dict[str, bytes]) -> bytes:
    """Return the bytes of an in-memory ZIP built from name -> content."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _register_search(*, has_suppl: str = "Y", open_access: str = "Y") -> None:
    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json={
            "resultList": {
                "result": [
                    {
                        "source": "PMC",
                        "id": "PMC123456",
                        "pmcid": "PMC123456",
                        "isOpenAccess": open_access,
                        "hasSuppl": has_suppl,
                    }
                ]
            }
        },
        status=200,
    )


# ---------------------------------------------------------------------------
# classify_supplement
# ---------------------------------------------------------------------------


def test_classify_supplement_by_extension() -> None:
    assert classify_supplement("Table_S1.csv") == SupplementKind.TABULAR
    assert classify_supplement("data.XLSX") == SupplementKind.TABULAR
    assert classify_supplement("methods.pdf") == SupplementKind.DOCUMENT
    assert classify_supplement("figure1.png") == SupplementKind.IMAGE
    assert classify_supplement("reads.fastq") == SupplementKind.SEQUENCE
    assert classify_supplement("bundle.tar") == SupplementKind.ARCHIVE
    assert classify_supplement("README") == SupplementKind.OTHER


# ---------------------------------------------------------------------------
# find_supplement_source_europepmc
# ---------------------------------------------------------------------------


@responses.activate
def test_find_supplement_source_reports_availability() -> None:
    _register_search(has_suppl="Y")
    info = find_supplement_source_europepmc(DOI)
    assert info["has_supplements"] is True
    assert info["pmcid"] == "PMC123456"
    assert info["zip_url"] == SUPPL_URL


@responses.activate
def test_find_supplement_source_no_record() -> None:
    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json={"resultList": {"result": []}},
        status=200,
    )
    info = find_supplement_source_europepmc(DOI)
    assert info["has_supplements"] is False
    assert info["error"]


# ---------------------------------------------------------------------------
# retrieve_supplements
# ---------------------------------------------------------------------------


@responses.activate
def test_retrieve_supplements_filters_and_inlines() -> None:
    _register_search()
    archive = _make_zip(
        {
            "Table_S1.csv": b"sample_id,depth\nA,10\nB,20\n",
            "methods.pdf": b"%PDF-1.4 fake pdf bytes",
            "figure1.png": b"\x89PNGfake",
            "reads.fastq": b"@r1\nACGT\n+\nIIII\n",
        }
    )
    responses.add(responses.GET, SUPPL_URL, body=archive, status=200)

    result = retrieve_supplements(DOI)

    kept = {f.filename: f for f in result.files}
    assert set(kept) == {"Table_S1.csv", "methods.pdf"}
    assert kept["Table_S1.csv"].text is not None
    assert "sample_id" in kept["Table_S1.csv"].text
    assert kept["methods.pdf"].saved_path is not None
    assert os.path.exists(kept["methods.pdf"].saved_path)

    skipped = {f.filename for f in result.skipped}
    assert skipped == {"figure1.png", "reads.fastq"}
    assert result.has_supplements is True
    assert result.error is None

    remove_temp_files([f.saved_path for f in result.files if f.saved_path])


@responses.activate
def test_retrieve_supplements_no_open_access_supplements() -> None:
    _register_search(has_suppl="N")
    result = retrieve_supplements(DOI)
    assert result.files == []
    assert result.error is not None
    # No download attempt should have been registered/needed.
    assert result.pmcid == "PMC123456"


@responses.activate
def test_retrieve_supplements_respects_max_files() -> None:
    _register_search()
    archive = _make_zip(
        {
            "a.csv": b"x,y\n1,2\n",
            "b.csv": b"x,y\n3,4\n",
            "c.csv": b"x,y\n5,6\n",
        }
    )
    responses.add(responses.GET, SUPPL_URL, body=archive, status=200)

    result = retrieve_supplements(DOI, max_files=2)
    assert len(result.files) == 2
    assert any("max_files" in (f.skipped_reason or "") for f in result.skipped)


@responses.activate
def test_retrieve_supplements_skips_oversized_file() -> None:
    _register_search()
    archive = _make_zip({"big.csv": b"a,b\n" + b"1,2\n" * 1000})
    responses.add(responses.GET, SUPPL_URL, body=archive, status=200)

    result = retrieve_supplements(DOI, max_file_bytes=10)
    assert result.files == []
    assert any("byte cap" in (f.skipped_reason or "") for f in result.skipped)


@responses.activate
def test_retrieve_supplements_bad_zip() -> None:
    _register_search()
    responses.add(responses.GET, SUPPL_URL, body=b"not a zip", status=200)
    result = retrieve_supplements(DOI)
    assert result.files == []
    assert result.error is not None


@responses.activate
def test_retrieve_supplements_custom_kinds() -> None:
    _register_search()
    archive = _make_zip(
        {
            "figure1.png": b"\x89PNGfake",
            "table.csv": b"x,y\n1,2\n",
        }
    )
    responses.add(responses.GET, SUPPL_URL, body=archive, status=200)

    result = retrieve_supplements(DOI, useful_kinds={SupplementKind.IMAGE})
    kept = {f.filename for f in result.files}
    assert kept == {"figure1.png"}

    remove_temp_files([f.saved_path for f in result.files if f.saved_path])
