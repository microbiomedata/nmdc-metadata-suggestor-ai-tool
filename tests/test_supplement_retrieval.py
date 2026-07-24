"""Tests for supplementary-material retrieval across Europe PMC, PMC OA, Dryad."""

import io
import os
import tarfile
import zipfile
from urllib.parse import quote

import pytest
import responses

from nmdc_metadata_suggestor_ai_tool.constants import (
    CROSSREF_API_URL,
    DATACITE_API_URL,
    DRYAD_API_URL,
    EUROPEPMC_API_URL,
    EUROPEPMC_FULLTEXT_XML_URL_TEMPLATE,
    EUROPEPMC_SUPPL_URL_TEMPLATE,
    FIGSHARE_API,
    PMC_OA_SERVICE_URL,
    ZENODO_API,
)
from nmdc_metadata_suggestor_ai_tool.models.supplement import SupplementKind
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import remove_temp_files
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.retrieve_supplements import (
    classify_supplement,
    extract_accessions_from_text,
    extract_dataset_dois_from_text,
    find_related_data_dois,
    find_supplement_source_europepmc,
    find_supplement_source_pmc_oa,
    is_dryad_doi,
    parse_supplement_captions,
    retrieve_supplements,
    retrieve_supplements_from_dryad,
    retrieve_supplements_from_europepmc,
    retrieve_supplements_from_figshare,
    retrieve_supplements_from_pmc_oa,
    retrieve_supplements_from_zenodo,
)

DOI = "10.1038/s41564-020-00861-0"
DRYAD_DOI = "10.5061/dryad.abc123"
ZENODO_DOI = "10.5281/zenodo.123456"
FIGSHARE_DOI = "10.6084/m9.figshare.123456"
SUPPL_URL = EUROPEPMC_SUPPL_URL_TEMPLATE.format(source="PMC", article_id="PMC123456")
FULLTEXT_URL = EUROPEPMC_FULLTEXT_XML_URL_TEMPLATE.format(source="PMC", article_id="PMC123456")
OA_TGZ_URL = "https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/aa/bb/PMC123456.tar.gz"


def _make_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _make_targz(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        for name, content in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


JATS_XML = """<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <body>
    <supplementary-material xlink:href="Table_S1.csv">
      <label>Table S1</label>
      <caption><p>Per-sample environmental metadata.</p></caption>
    </supplementary-material>
  </body>
</article>"""


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
# classify_supplement / parse_supplement_captions
# ---------------------------------------------------------------------------


def test_classify_supplement_by_extension() -> None:
    assert classify_supplement("Table_S1.csv") == SupplementKind.TABULAR
    assert classify_supplement("data.XLSX") == SupplementKind.TABULAR
    assert classify_supplement("methods.pdf") == SupplementKind.DOCUMENT
    assert classify_supplement("figure1.png") == SupplementKind.IMAGE
    assert classify_supplement("reads.fastq") == SupplementKind.SEQUENCE
    assert classify_supplement("bundle.tar") == SupplementKind.ARCHIVE
    assert classify_supplement("README") == SupplementKind.OTHER


def test_parse_supplement_captions() -> None:
    captions = parse_supplement_captions(JATS_XML)
    assert captions["table_s1"].startswith("Table S1")
    assert "environmental metadata" in captions["table_s1"]


def test_parse_supplement_captions_bad_xml_returns_empty() -> None:
    assert parse_supplement_captions("<not-closed>") == {}


def test_parse_supplement_captions_rejects_doctype_entities() -> None:
    # A billion-laughs style payload must be refused before parsing.
    malicious = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>'
        "<article>&lol2;</article>"
    )
    assert parse_supplement_captions(malicious) == {}


def test_is_dryad_doi() -> None:
    assert is_dryad_doi(DRYAD_DOI) is True
    assert is_dryad_doi("https://doi.org/10.5061/dryad.x") is True
    assert is_dryad_doi(DOI) is False


# ---------------------------------------------------------------------------
# Europe PMC
# ---------------------------------------------------------------------------


@responses.activate
def test_find_supplement_source_reports_availability() -> None:
    _register_search(has_suppl="Y")
    info = find_supplement_source_europepmc(DOI)
    assert info["has_supplements"] is True
    assert info["pmcid"] == "PMC123456"
    assert info["zip_url"] == SUPPL_URL


@responses.activate
def test_europepmc_filters_inlines_and_captions() -> None:
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
    responses.add(responses.GET, FULLTEXT_URL, body=JATS_XML, status=200)

    result = retrieve_supplements_from_europepmc(DOI)

    kept = {f.filename: f for f in result.files}
    assert set(kept) == {"Table_S1.csv", "methods.pdf"}
    assert "sample_id" in (kept["Table_S1.csv"].text or "")
    assert kept["Table_S1.csv"].caption is not None
    assert "environmental metadata" in kept["Table_S1.csv"].caption
    assert kept["methods.pdf"].saved_path is not None
    assert {f.filename for f in result.skipped} == {"figure1.png", "reads.fastq"}
    assert result.has_supplements is True

    remove_temp_files([f.saved_path for f in result.files if f.saved_path])


@responses.activate
def test_europepmc_no_open_access_supplements() -> None:
    _register_search(has_suppl="N")
    result = retrieve_supplements_from_europepmc(DOI)
    assert result.files == []
    assert result.error is not None
    assert result.pmcid == "PMC123456"


@responses.activate
def test_europepmc_respects_max_files() -> None:
    _register_search()
    archive = _make_zip({f"{c}.csv": b"x,y\n1,2\n" for c in ("a", "b", "c")})
    responses.add(responses.GET, SUPPL_URL, body=archive, status=200)
    responses.add(responses.GET, FULLTEXT_URL, body=b"<article/>", status=200)

    result = retrieve_supplements_from_europepmc(DOI, max_files=2)
    assert len(result.files) == 2
    assert any("max_files" in (f.skipped_reason or "") for f in result.skipped)


@responses.activate
def test_europepmc_skips_oversized_file() -> None:
    _register_search()
    archive = _make_zip({"big.csv": b"a,b\n" + b"1,2\n" * 1000})
    responses.add(responses.GET, SUPPL_URL, body=archive, status=200)
    responses.add(responses.GET, FULLTEXT_URL, body=b"<article/>", status=200)

    result = retrieve_supplements_from_europepmc(DOI, max_file_bytes=10)
    assert result.files == []
    assert any("byte cap" in (f.skipped_reason or "") for f in result.skipped)


@responses.activate
def test_europepmc_bad_zip() -> None:
    _register_search()
    responses.add(responses.GET, SUPPL_URL, body=b"not a zip", status=200)
    result = retrieve_supplements_from_europepmc(DOI, include_captions=False)
    assert result.files == []
    assert result.error is not None


# ---------------------------------------------------------------------------
# NCBI PMC OA
# ---------------------------------------------------------------------------

OA_RECORD_XML = """<?xml version="1.0"?>
<OA>
  <records>
    <record id="PMC123456">
      <link format="tgz" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/aa/bb/PMC123456.tar.gz"/>
    </record>
  </records>
</OA>"""


def _register_oa_service() -> None:
    responses.add(responses.GET, PMC_OA_SERVICE_URL, body=OA_RECORD_XML, status=200)


@responses.activate
def test_find_supplement_source_pmc_oa() -> None:
    _register_oa_service()
    info = find_supplement_source_pmc_oa("PMC123456")
    assert info["tgz_url"] == OA_TGZ_URL
    assert "error" not in info


@responses.activate
def test_find_supplement_source_pmc_oa_error() -> None:
    responses.add(
        responses.GET,
        PMC_OA_SERVICE_URL,
        body='<OA><error code="idDoesNotExist">bad</error></OA>',
        status=200,
    )
    info = find_supplement_source_pmc_oa("PMC000")
    assert info["tgz_url"] is None
    assert info["error"]


@responses.activate
def test_pmc_oa_retrieves_and_captions_from_nxml() -> None:
    _register_oa_service()
    targz = _make_targz(
        {
            "PMC123456/table_s1.csv": b"sample_id,ph\nA,7\n",
            "PMC123456/figure1.jpg": b"\xff\xd8fakejpeg",
            "PMC123456/main.nxml": JATS_XML.replace("Table_S1.csv", "table_s1.csv").encode(),
        }
    )
    responses.add(responses.GET, OA_TGZ_URL, body=targz, status=200)

    result = retrieve_supplements_from_pmc_oa("PMC123456", doi=DOI)
    kept = {os.path.basename(f.filename): f for f in result.files}
    assert set(kept) == {"table_s1.csv"}
    assert kept["table_s1.csv"].caption is not None
    assert any(f.kind == SupplementKind.IMAGE for f in result.skipped)


# ---------------------------------------------------------------------------
# Dryad
# ---------------------------------------------------------------------------


def _register_dryad(files: list[dict]) -> None:
    dataset_url = f"{DRYAD_API_URL}/datasets/{quote(f'doi:{DRYAD_DOI}', safe='')}"
    responses.add(
        responses.GET,
        dataset_url,
        json={"_links": {"stash:version": {"href": "/api/v2/versions/999"}}},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{DRYAD_API_URL}/versions/999/files",
        json={"_embedded": {"stash:files": files}},
        status=200,
    )


@responses.activate
def test_dryad_retrieves_tabular_files() -> None:
    _register_dryad(
        [
            {
                "path": "samples.csv",
                "size": 24,
                "description": "Per-sample metadata table",
                "_links": {"stash:download": {"href": "/api/v2/files/1/download"}},
            },
            {
                "path": "photo.jpg",
                "size": 10,
                "_links": {"stash:download": {"href": "/api/v2/files/2/download"}},
            },
        ]
    )
    responses.add(
        responses.GET,
        f"{DRYAD_API_URL}/files/1/download",
        body=b"sample_id,site\nA,north\n",
        status=200,
    )

    result = retrieve_supplements_from_dryad(DRYAD_DOI)
    assert [f.filename for f in result.files] == ["samples.csv"]
    assert result.files[0].caption == "Per-sample metadata table"
    assert "site" in (result.files[0].text or "")
    assert {f.filename for f in result.skipped} == {"photo.jpg"}


@responses.activate
def test_dryad_no_files() -> None:
    _register_dryad([])
    result = retrieve_supplements_from_dryad(DRYAD_DOI)
    assert result.files == []
    assert result.error is not None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@responses.activate
def test_orchestrator_falls_back_to_pmc_oa() -> None:
    # Europe PMC reports no supplements -> fall back to the NCBI OA package.
    _register_search(has_suppl="N")
    _register_oa_service()
    targz = _make_targz({"PMC123456/data.csv": b"a,b\n1,2\n"})
    responses.add(responses.GET, OA_TGZ_URL, body=targz, status=200)

    result = retrieve_supplements(DOI, follow_related=False)
    assert result.source == "pmc_oa"
    assert [os.path.basename(f.filename) for f in result.files] == ["data.csv"]
    assert result.files[0].source == "pmc_oa"
    assert "europepmc" in result.attempts and "pmc_oa" in result.attempts


@responses.activate
def test_orchestrator_routes_dryad_doi() -> None:
    _register_dryad(
        [
            {
                "path": "meta.tsv",
                "size": 12,
                "_links": {"stash:download": {"href": "/api/v2/files/9/download"}},
            }
        ]
    )
    responses.add(
        responses.GET,
        f"{DRYAD_API_URL}/files/9/download",
        body=b"id\tval\n1\t2\n",
        status=200,
    )
    result = retrieve_supplements(DRYAD_DOI)
    assert result.source == "dryad"
    assert [f.filename for f in result.files] == ["meta.tsv"]


# ---------------------------------------------------------------------------
# Zenodo
# ---------------------------------------------------------------------------


@responses.activate
def test_zenodo_retrieves_files() -> None:
    responses.add(
        responses.GET,
        ZENODO_API,
        json={
            "hits": {
                "hits": [
                    {
                        "doi": ZENODO_DOI,
                        "files": [
                            {
                                "key": "samples.csv",
                                "size": 20,
                                "links": {
                                    "self": "https://zenodo.org/api/records/1/files/samples.csv/content"
                                },
                            },
                            {
                                "key": "movie.mp4",
                                "size": 5,
                                "links": {
                                    "self": "https://zenodo.org/api/records/1/files/movie.mp4/content"
                                },
                            },
                        ],
                    }
                ]
            }
        },
        status=200,
    )
    responses.add(
        responses.GET,
        "https://zenodo.org/api/records/1/files/samples.csv/content",
        body=b"id,site\n1,north\n",
        status=200,
    )
    result = retrieve_supplements_from_zenodo(ZENODO_DOI)
    assert [f.filename for f in result.files] == ["samples.csv"]
    assert result.files[0].source == "zenodo"
    assert "site" in (result.files[0].text or "")
    assert {f.filename for f in result.skipped} == {"movie.mp4"}


# ---------------------------------------------------------------------------
# Figshare
# ---------------------------------------------------------------------------


@responses.activate
def test_figshare_retrieves_files() -> None:
    responses.add(
        responses.GET,
        FIGSHARE_API,
        json=[{"id": 42, "url_public_api": "https://api.figshare.com/v2/articles/42"}],
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.figshare.com/v2/articles/42",
        json={
            "files": [
                {
                    "name": "table.csv",
                    "size": 18,
                    "download_url": "https://ndownloader.figshare.com/files/1",
                },
                {
                    "name": "fig.png",
                    "size": 9,
                    "download_url": "https://ndownloader.figshare.com/files/2",
                },
            ]
        },
        status=200,
    )
    responses.add(
        responses.GET,
        "https://ndownloader.figshare.com/files/1",
        body=b"a,b\n1,2\n",
        status=200,
    )
    result = retrieve_supplements_from_figshare(FIGSHARE_DOI)
    assert [f.filename for f in result.files] == ["table.csv"]
    assert result.files[0].source == "figshare"
    assert {f.filename for f in result.skipped} == {"fig.png"}


# ---------------------------------------------------------------------------
# Related-identifier following + text/accession mining
# ---------------------------------------------------------------------------


@responses.activate
def test_find_related_data_dois_from_crossref_and_datacite() -> None:
    responses.add(
        responses.GET,
        f"{CROSSREF_API_URL}/{DOI}",
        json={
            "message": {
                "relation": {
                    "is-supplemented-by": [{"id": ZENODO_DOI, "id-type": "doi"}],
                    "references": [{"id": "10.1000/other", "id-type": "doi"}],
                }
            }
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"{DATACITE_API_URL}/{DOI}",
        json={
            "data": {
                "attributes": {
                    "relatedIdentifiers": [
                        {
                            "relatedIdentifier": FIGSHARE_DOI,
                            "relatedIdentifierType": "DOI",
                            "relationType": "HasPart",
                        }
                    ]
                }
            }
        },
        status=200,
    )
    related = find_related_data_dois(DOI)
    # Zenodo (is-supplemented-by) and Figshare (HasPart) kept; plain citation dropped.
    assert ZENODO_DOI in related
    assert FIGSHARE_DOI in related
    assert "10.1000/other" not in related


def test_extract_dataset_dois_from_text() -> None:
    text = (
        "Data are available at Zenodo (https://doi.org/10.5281/zenodo.987654) and "
        "Dryad doi:10.5061/dryad.zzz. See also the paper 10.1038/nature12373."
    )
    dois = extract_dataset_dois_from_text(text)
    assert "10.5281/zenodo.987654" in dois
    assert "10.5061/dryad.zzz" in dois
    assert "10.1038/nature12373" not in dois  # not a data repo


def test_extract_dataset_dois_trims_trailing_prose() -> None:
    # DOIs abutting following text (no delimiter) must not absorb the prose.
    text = (
        "deposited at 10.5281/zenodo.987654and then analysed; "
        "versioned dataset 10.6084/m9.figshare.42.v2, done."
    )
    dois = extract_dataset_dois_from_text(text)
    assert "10.5281/zenodo.987654" in dois
    assert "10.6084/m9.figshare.42.v2" in dois
    assert all(d.isascii() and "and" not in d and "," not in d for d in dois)


def test_extract_accessions_from_text() -> None:
    text = (
        "Reads deposited under BioProject PRJNA123456 (runs SRR1234567, SRR1234568) and GSE99999."
    )
    accessions = extract_accessions_from_text(text)
    assert "PRJNA123456" in accessions
    assert "SRR1234567" in accessions
    assert "GSE99999" in accessions


@responses.activate
def test_orchestrator_aggregates_hosted_and_related() -> None:
    # Europe PMC yields an SI table; a related Zenodo dataset adds another.
    _register_search()
    responses.add(
        responses.GET, SUPPL_URL, body=_make_zip({"Table_S1.csv": b"a,b\n1,2\n"}), status=200
    )
    responses.add(responses.GET, FULLTEXT_URL, body=b"<article/>", status=200)
    responses.add(
        responses.GET,
        f"{CROSSREF_API_URL}/{DOI}",
        json={
            "message": {"relation": {"is-supplemented-by": [{"id": ZENODO_DOI, "id-type": "doi"}]}}
        },
        status=200,
    )
    responses.add(
        responses.GET, f"{DATACITE_API_URL}/{DOI}", json={"data": {"attributes": {}}}, status=200
    )
    responses.add(
        responses.GET,
        ZENODO_API,
        json={
            "hits": {
                "hits": [
                    {
                        "doi": ZENODO_DOI,
                        "files": [
                            {
                                "key": "zenodo_data.csv",
                                "size": 12,
                                "links": {
                                    "self": "https://zenodo.org/api/records/9/files/zenodo_data.csv/content"
                                },
                            }
                        ],
                    }
                ]
            }
        },
        status=200,
    )
    responses.add(
        responses.GET,
        "https://zenodo.org/api/records/9/files/zenodo_data.csv/content",
        body=b"x,y\n1,2\n",
        status=200,
    )

    result = retrieve_supplements(DOI)
    names = {f.filename for f in result.files}
    assert names == {"Table_S1.csv", "zenodo_data.csv"}
    sources = {f.source for f in result.files}
    assert sources == {"europepmc", "zenodo"}
    assert result.source == "europepmc+zenodo"


@responses.activate
def test_orchestrator_shares_budget_across_sources() -> None:
    # Hosted SI uses 1 of a max_files=2 budget; the linked Zenodo dataset must be
    # bounded to the remaining 1 file, so its second file is skipped *by cap* and
    # never downloaded (its download URL is intentionally left unregistered).
    _register_search()
    responses.add(
        responses.GET, SUPPL_URL, body=_make_zip({"Table_S1.csv": b"a,b\n1,2\n"}), status=200
    )
    responses.add(responses.GET, FULLTEXT_URL, body=b"<article/>", status=200)
    responses.add(
        responses.GET,
        f"{CROSSREF_API_URL}/{DOI}",
        json={
            "message": {"relation": {"is-supplemented-by": [{"id": ZENODO_DOI, "id-type": "doi"}]}}
        },
        status=200,
    )
    responses.add(
        responses.GET, f"{DATACITE_API_URL}/{DOI}", json={"data": {"attributes": {}}}, status=200
    )
    responses.add(
        responses.GET,
        ZENODO_API,
        json={
            "hits": {
                "hits": [
                    {
                        "doi": ZENODO_DOI,
                        "files": [
                            {
                                "key": "z1.csv",
                                "size": 8,
                                "links": {"self": "https://zenodo.org/z1"},
                            },
                            {
                                "key": "z2.csv",
                                "size": 8,
                                "links": {"self": "https://zenodo.org/z2"},
                            },
                        ],
                    }
                ]
            }
        },
        status=200,
    )
    responses.add(responses.GET, "https://zenodo.org/z1", body=b"x\n1\n", status=200)
    # NOTE: https://zenodo.org/z2 is deliberately NOT registered.

    result = retrieve_supplements(DOI, max_files=2)

    assert len(result.files) == 2
    assert {f.filename for f in result.files} == {"Table_S1.csv", "z1.csv"}
    # z2 was dropped by the shared budget (max_files), not attempted/read.
    z2_skips = [f for f in result.skipped if f.filename == "z2.csv"]
    assert z2_skips and "max_files" in (z2_skips[0].skipped_reason or "")


# ---------------------------------------------------------------------------
# Merge cleanup (no orphaned temp files)
# ---------------------------------------------------------------------------


def test_merge_results_deletes_trimmed_temp_files(tmp_path) -> None:
    from nmdc_metadata_suggestor_ai_tool.models.supplement import (
        SupplementFile,
        SupplementRetrievalResult,
    )
    from nmdc_metadata_suggestor_ai_tool.publication_ingestion.retrieve_supplements import (
        _merge_results,
    )

    kept_path = tmp_path / "kept.pdf"
    kept_path.write_bytes(b"%PDF-1.4 kept")
    dropped_path = tmp_path / "dropped.pdf"
    dropped_path.write_bytes(b"%PDF-1.4 dropped")

    r1 = SupplementRetrievalResult(
        doi="x",
        source="europepmc",
        files=[
            SupplementFile(
                filename="kept.pdf",
                kind=SupplementKind.DOCUMENT,
                source="europepmc",
                size_bytes=13,
                saved_path=str(kept_path),
            )
        ],
    )
    r2 = SupplementRetrievalResult(
        doi="x",
        source="zenodo",
        files=[
            SupplementFile(
                filename="dropped.pdf",
                kind=SupplementKind.DOCUMENT,
                source="zenodo",
                size_bytes=16,
                saved_path=str(dropped_path),
            )
        ],
    )

    # max_files=1 forces the second file to be trimmed from the union.
    merged = _merge_results("x", [r1, r2], [], max_files=1, max_total_bytes=1_000_000)

    assert [f.filename for f in merged.files] == ["kept.pdf"]
    assert kept_path.exists()  # kept file's temp path untouched
    assert not dropped_path.exists()  # trimmed file's temp path deleted (no leak)


# ---------------------------------------------------------------------------
# Record / link-selection hardening (#4)
# ---------------------------------------------------------------------------


@responses.activate
def test_zenodo_prefers_content_link() -> None:
    # InvenioRDM exposes downloads under links.content; prefer it over self.
    responses.add(
        responses.GET,
        ZENODO_API,
        json={
            "hits": {
                "hits": [
                    {
                        "doi": ZENODO_DOI,
                        "files": [
                            {
                                "key": "d.csv",
                                "size": 8,
                                "links": {
                                    "content": "https://zenodo.org/content/d.csv",
                                    "self": "https://zenodo.org/self/d.csv",
                                },
                            }
                        ],
                    }
                ]
            }
        },
        status=200,
    )
    # Only the content URL is registered; using self would fail.
    responses.add(responses.GET, "https://zenodo.org/content/d.csv", body=b"a,b\n1,2\n", status=200)

    result = retrieve_supplements_from_zenodo(ZENODO_DOI)
    assert [f.filename for f in result.files] == ["d.csv"]


@responses.activate
def test_zenodo_does_not_guess_among_unrelated_hits() -> None:
    responses.add(
        responses.GET,
        ZENODO_API,
        json={
            "hits": {
                "hits": [
                    {"doi": "10.5281/zenodo.111", "files": [{"key": "a.csv", "size": 4}]},
                    {"doi": "10.5281/zenodo.222", "files": [{"key": "b.csv", "size": 4}]},
                ]
            }
        },
        status=200,
    )
    result = retrieve_supplements_from_zenodo(ZENODO_DOI)  # matches neither
    assert result.files == []
    assert result.error


@responses.activate
def test_figshare_picks_doi_matching_record() -> None:
    responses.add(
        responses.GET,
        FIGSHARE_API,
        json=[
            {
                "id": 1,
                "doi": "10.6084/m9.figshare.999",
                "url_public_api": "https://api.figshare.com/v2/articles/1",
            },
            {
                "id": 42,
                "doi": FIGSHARE_DOI,
                "url_public_api": "https://api.figshare.com/v2/articles/42",
            },
        ],
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.figshare.com/v2/articles/42",
        json={
            "files": [
                {
                    "name": "t.csv",
                    "size": 8,
                    "download_url": "https://ndownloader.figshare.com/files/9",
                }
            ]
        },
        status=200,
    )
    responses.add(
        responses.GET, "https://ndownloader.figshare.com/files/9", body=b"a\n1\n", status=200
    )
    result = retrieve_supplements_from_figshare(FIGSHARE_DOI)
    assert [f.filename for f in result.files] == ["t.csv"]


# ---------------------------------------------------------------------------
# Live smoke tests (opt-in): verify the retrievers against real repository APIs.
# Deselected by default; run with `-m integration` and the env vars set, e.g.
#   NMDC_TEST_ZENODO_DOI=10.5281/zenodo.XXXX uv run pytest -m integration
# ---------------------------------------------------------------------------

integration = pytest.mark.integration


def _assert_live_result(result, source: str) -> None:
    from nmdc_metadata_suggestor_ai_tool.models.supplement import SupplementRetrievalResult

    assert isinstance(result, SupplementRetrievalResult)
    assert result.source == source
    for f in result.files:
        assert f.source == source
        assert f.text is not None or f.saved_path is not None
    remove_temp_files([f.saved_path for f in result.files if f.saved_path])


@integration
def test_zenodo_live() -> None:
    doi = os.environ.get("NMDC_TEST_ZENODO_DOI")
    if not doi:
        pytest.skip("set NMDC_TEST_ZENODO_DOI to run")
    _assert_live_result(retrieve_supplements_from_zenodo(doi), "zenodo")


@integration
def test_figshare_live() -> None:
    doi = os.environ.get("NMDC_TEST_FIGSHARE_DOI")
    if not doi:
        pytest.skip("set NMDC_TEST_FIGSHARE_DOI to run")
    _assert_live_result(retrieve_supplements_from_figshare(doi), "figshare")


@integration
def test_dryad_live() -> None:
    doi = os.environ.get("NMDC_TEST_DRYAD_DOI")
    if not doi:
        pytest.skip("set NMDC_TEST_DRYAD_DOI to run")
    _assert_live_result(retrieve_supplements_from_dryad(doi), "dryad")
