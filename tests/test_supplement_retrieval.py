"""Tests for supplementary-material retrieval across Europe PMC, Dryad, Zenodo, Figshare."""

import io
import os
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

import pytest
import requests
import responses

from nmdc_metadata_suggestor_ai_tool.constants import (
    CROSSREF_API_URL,
    DATACITE_API_URL,
    DEFAULT_TIMEOUT,
    DRYAD_API_URL,
    EUROPEPMC_API_URL,
    EUROPEPMC_FULLTEXT_XML_URL_TEMPLATE,
    EUROPEPMC_SUPPL_URL_TEMPLATE,
    FIGSHARE_API,
    ZENODO_API,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import request_with_retry
from nmdc_metadata_suggestor_ai_tool.models.supplement import SupplementKind
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import remove_temp_files
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements import (
    classify_supplement,
    extract_accessions_from_text,
    extract_dataset_dois_from_text,
    find_related_data_dois,
    find_supplement_source_europepmc,
    is_dryad_doi,
    parse_supplement_captions,
    retrieve_supplements,
    retrieve_supplements_from_dryad,
    retrieve_supplements_from_europepmc,
    retrieve_supplements_from_figshare,
    retrieve_supplements_from_zenodo,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.retrieve import (
    is_removable_temp_file,
    merge_results,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.shared import (
    Member,
    download_bounded,
    select_members,
)

DOI = "10.1038/s41564-020-00861-0"
DRYAD_DOI = "10.5061/dryad.abc123"
ZENODO_DOI = "10.5281/zenodo.123456"
FIGSHARE_DOI = "10.6084/m9.figshare.123456"
SUPPL_URL = EUROPEPMC_SUPPL_URL_TEMPLATE.format(pmcid="PMC123456")
FULLTEXT_URL = EUROPEPMC_FULLTEXT_XML_URL_TEMPLATE.format(pmcid="PMC123456")


def _make_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
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


def test_select_members_caps_apply_to_unknown_reported_size() -> None:
    # Sources that report size 0 (no Content-Length) must not bypass the budget.
    members = [
        Member(name="a.csv", size=0, read=lambda: b"x" * 40),
        Member(name="b.csv", size=0, read=lambda: b"y" * 40),
        Member(name="c.csv", size=0, read=lambda: b"z" * 40),
    ]
    kept, skipped = select_members(
        members,
        kept_kinds=frozenset({SupplementKind.TABULAR}),
        max_files=10,
        max_file_bytes=100,
        max_total_bytes=100,
        max_text_chars=1000,
        save_dir=None,
        captions=None,
    )
    assert [f.filename for f in kept] == ["a.csv", "b.csv"]
    assert [f.size_bytes for f in kept] == [40, 40]  # actual bytes, not the reported 0
    assert any("max_total_bytes" in (f.skipped_reason or "") for f in skipped)


def test_select_members_rejects_member_larger_than_reported() -> None:
    # A member that under-reports its size is dropped after the read rather than
    # being materialized past the per-file cap.
    kept, skipped = select_members(
        [Member(name="big.csv", size=0, read=lambda: b"x" * 500)],
        kept_kinds=frozenset({SupplementKind.TABULAR}),
        max_files=10,
        max_file_bytes=100,
        max_total_bytes=1000,
        max_text_chars=1000,
        save_dir=None,
        captions=None,
    )
    assert kept == []
    assert "byte cap" in (skipped[0].skipped_reason or "")


def test_select_members_spends_max_files_on_data_like_kinds() -> None:
    # The source lists documents first, but a cap this tight must go to the
    # spreadsheets -- they carry the per-sample values we are after.
    members = [
        Member(name="methods.txt", size=4, read=lambda: b"aaaa"),
        Member(name="notes.txt", size=4, read=lambda: b"bbbb"),
        Member(name="Table_S1.csv", size=4, read=lambda: b"a,b\n"),
        Member(name="Table_S2.csv", size=4, read=lambda: b"c,d\n"),
    ]
    kept, skipped = select_members(
        members,
        kept_kinds=frozenset({SupplementKind.TABULAR, SupplementKind.DOCUMENT}),
        max_files=2,
        max_file_bytes=100,
        max_total_bytes=1000,
        max_text_chars=1000,
        save_dir=None,
        captions=None,
    )
    # Ranking is by kind first, then the source's own order within a kind.
    assert [f.filename for f in kept] == ["Table_S1.csv", "Table_S2.csv"]
    assert {f.filename for f in skipped} == {"methods.txt", "notes.txt"}
    assert all("max_files" in (f.skipped_reason or "") for f in skipped)


def test_select_members_spends_max_total_bytes_on_data_like_kinds() -> None:
    # The byte cap is ranked the same way: a big document must not crowd out a
    # table that was listed after it.
    kept, _skipped = select_members(
        [
            Member(name="methods.pdf", size=80, read=lambda: b"%PDF" + b"x" * 76),
            Member(name="Table_S1.csv", size=40, read=lambda: b"a,b\n" * 10),
        ],
        kept_kinds=frozenset({SupplementKind.TABULAR, SupplementKind.DOCUMENT}),
        max_files=10,
        max_file_bytes=100,
        max_total_bytes=100,
        max_text_chars=1000,
        save_dir=None,
        captions=None,
    )
    assert [f.filename for f in kept] == ["Table_S1.csv"]


def test_select_members_keeps_colliding_basenames_apart(tmp_path: Path) -> None:
    # Two members share a basename (one nested in an archive). With save_dir set
    # they must not write to the same path, or the first file's bytes are lost
    # and both results point at the second.
    kept, _skipped = select_members(
        [
            Member(name="figures/Table_S1.xlsx", size=4, read=lambda: b"aaaa"),
            Member(name="Table_S1.xlsx", size=4, read=lambda: b"bbbb"),
        ],
        kept_kinds=frozenset({SupplementKind.TABULAR}),
        max_files=10,
        max_file_bytes=100,
        max_total_bytes=1000,
        max_text_chars=1000,
        save_dir=str(tmp_path),
        captions=None,
    )
    paths = [f.saved_path for f in kept]
    assert all(p is not None for p in paths)
    assert len(paths) == 2
    assert len(set(paths)) == 2, "colliding basenames overwrote each other"
    assert {Path(str(p)).read_bytes() for p in paths} == {b"aaaa", b"bbbb"}


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
# Bounded downloads
# ---------------------------------------------------------------------------


@responses.activate
def test_download_bounded_errors_are_source_agnostic() -> None:
    # The same helper fetches archives and single files (Zenodo/Figshare), so the
    # messages must not claim every oversized body is an "Archive".
    url = "https://example.org/supplement.xlsx"
    responses.add(
        responses.GET, url, body=b"x" * 500, status=200, headers={"Content-Length": "500"}
    )
    with pytest.raises(RuntimeError, match=r"^Download size 500 bytes exceeds cap 10 bytes$"):
        download_bounded(url, 10)

    # With no declared length the cap trips mid-stream instead.
    responses.reset()
    responses.add(responses.GET, url, body=io.BufferedReader(io.BytesIO(b"x" * 500)), status=200)
    with pytest.raises(RuntimeError, match=r"^Download exceeded size cap of 10 bytes$"):
        download_bounded(url, 10)


@responses.activate
def test_download_bounded_wraps_connection_errors() -> None:
    # A network failure must arrive as RuntimeError like every other failure:
    # callers catch that alone, and a bare RequestException would escape them.
    url = "https://example.org/supplement.xlsx"
    responses.add(responses.GET, url, body=requests.ConnectionError("connection reset"))
    with pytest.raises(RuntimeError, match="Failed to download supplements"):
        download_bounded(url, 1000)


@responses.activate
def test_europepmc_reports_download_failure_instead_of_raising() -> None:
    # An agentic caller gets a result carrying .error, not an exception.
    _register_search()
    responses.add(responses.GET, SUPPL_URL, body=requests.ConnectionError("connection reset"))
    result = retrieve_supplements_from_europepmc(DOI)
    assert result.files == []
    assert "Failed to download supplements" in (result.error or "")


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
def test_europepmc_zip_url_keys_on_pmcid_not_source_id() -> None:
    # The endpoint takes a bare PMCID with no source-DB path segment. Most search
    # hits are MED-sourced, so building the URL from ``source``/``id`` yields
    # ``/MED/<pmid>/supplementaryFiles``, which 404s for every article. Asserted
    # as a literal because a mocked request would match whatever URL we invent.
    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json={
            "resultList": {
                "result": [
                    {
                        "source": "MED",
                        "id": "33526884",
                        "pmcid": "PMC8007473",
                        "isOpenAccess": "Y",
                        "hasSuppl": "Y",
                    }
                ]
            }
        },
        status=200,
    )
    info = find_supplement_source_europepmc(DOI)
    assert info["zip_url"] == (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC8007473/supplementaryFiles"
    )


@responses.activate
def test_europepmc_without_pmcid_has_no_zip_url() -> None:
    # A record that is not in PMC has no PMCID to key the endpoint on.
    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json={
            "resultList": {
                "result": [{"source": "MED", "id": "999", "isOpenAccess": "Y", "hasSuppl": "Y"}]
            }
        },
        status=200,
    )
    info = find_supplement_source_europepmc(DOI)
    assert info["zip_url"] is None

    result = retrieve_supplements_from_europepmc(DOI)
    assert result.files == []
    assert result.error is not None


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
def test_europepmc_skips_download_for_non_open_access() -> None:
    # ``supplementaryFiles`` is OA-scoped: a non-OA record must not be fetched
    # even when the record claims to have supplements.
    _register_search(has_suppl="Y", open_access="N")
    result = retrieve_supplements_from_europepmc(DOI)
    assert result.files == []
    assert "not open access" in (result.error or "")
    # Only the metadata lookup was issued -- no ZIP download attempt.
    assert len(responses.calls) == 1


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
def test_europepmc_max_files_goes_to_tabular_over_documents() -> None:
    # The archive lists its documents first; the cap must still land on the
    # spreadsheets, which is where the per-sample metadata lives.
    _register_search()
    archive = _make_zip(
        {
            "methods.pdf": b"%PDF-1.4 methods",
            "protocol.pdf": b"%PDF-1.4 protocol",
            "Table_S1.csv": b"sample_id,depth\nA,10\n",
            "Table_S2.csv": b"sample_id,ph\nA,7\n",
        }
    )
    responses.add(responses.GET, SUPPL_URL, body=archive, status=200)
    responses.add(responses.GET, FULLTEXT_URL, body=b"<article/>", status=200)

    result = retrieve_supplements_from_europepmc(DOI, max_files=2)

    assert [f.filename for f in result.files] == ["Table_S1.csv", "Table_S2.csv"]
    assert {f.filename for f in result.skipped} == {"methods.pdf", "protocol.pdf"}


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
def test_dryad_via_zenodo_mirror() -> None:
    # Dryad's own download is auth-gated, so content comes from the Zenodo mirror.
    responses.add(
        responses.GET,
        ZENODO_API,
        json={
            "hits": {
                "hits": [
                    {
                        "doi": DRYAD_DOI,
                        "files": [
                            {
                                "key": "samples.csv",
                                "size": 20,
                                "links": {"self": "https://zenodo.org/m/samples.csv"},
                            },
                            {
                                "key": "photo.jpg",
                                "size": 10,
                                "links": {"self": "https://zenodo.org/m/photo.jpg"},
                            },
                        ],
                    }
                ]
            }
        },
        status=200,
    )
    # photo.jpg is a low-value kind: skipped before download, so its URL is unused.
    responses.add(
        responses.GET,
        "https://zenodo.org/m/samples.csv",
        body=b"sample_id,site\nA,north\n",
        status=200,
    )

    result = retrieve_supplements_from_dryad(DRYAD_DOI)
    assert result.source == "dryad"
    assert [f.filename for f in result.files] == ["samples.csv"]
    assert result.files[0].source == "dryad"
    assert "site" in (result.files[0].text or "")
    assert "zenodo-mirror" in result.attempts
    assert {f.filename for f in result.skipped} == {"photo.jpg"}


@responses.activate
def test_dryad_list_only_when_no_mirror() -> None:
    # No Zenodo mirror -> list the Dryad files marked download-gated, don't fetch.
    responses.add(responses.GET, ZENODO_API, json={"hits": {"hits": []}}, status=200)
    _register_dryad([{"path": "samples.csv", "size": 24}, {"path": "photo.jpg", "size": 10}])
    result = retrieve_supplements_from_dryad(DRYAD_DOI)
    assert result.files == []
    assert result.error
    gated = [f for f in result.skipped if f.filename == "samples.csv"]
    assert gated and "gated" in (gated[0].skipped_reason or "")


@responses.activate
def test_dryad_no_files() -> None:
    responses.add(responses.GET, ZENODO_API, json={"hits": {"hits": []}}, status=200)
    _register_dryad([])
    result = retrieve_supplements_from_dryad(DRYAD_DOI)
    assert result.files == []
    assert result.error is not None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@responses.activate
def test_orchestrator_routes_dryad_doi() -> None:
    # A Dryad DOI routes to the Dryad retriever, which fetches via the Zenodo mirror.
    responses.add(
        responses.GET,
        ZENODO_API,
        json={
            "hits": {
                "hits": [
                    {
                        "doi": DRYAD_DOI,
                        "files": [
                            {
                                "key": "meta.tsv",
                                "size": 12,
                                "links": {"self": "https://zenodo.org/m/meta.tsv"},
                            }
                        ],
                    }
                ]
            }
        },
        status=200,
    )
    responses.add(
        responses.GET, "https://zenodo.org/m/meta.tsv", body=b"id\tval\n1\t2\n", status=200
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


def test_merge_results_deletes_trimmed_temp_files(tmp_path: Path) -> None:
    from nmdc_metadata_suggestor_ai_tool.models.supplement import (
        SupplementFile,
        SupplementRetrievalResult,
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
    merged = merge_results("x", [r1, r2], [], max_files=1, max_total_bytes=1_000_000)

    assert [f.filename for f in merged.files] == ["kept.pdf"]
    assert kept_path.exists()  # kept file's temp path untouched
    assert not dropped_path.exists()  # trimmed file's temp path deleted (no leak)


def test_merge_results_prefers_data_like_files_across_sources() -> None:
    # The global cap is spent on kind, not on which source happened to run first:
    # a linked dataset's spreadsheet outranks a hosted document.
    from nmdc_metadata_suggestor_ai_tool.models.supplement import (
        SupplementFile,
        SupplementRetrievalResult,
    )

    def _file(name: str, kind: SupplementKind, source: str) -> SupplementFile:
        return SupplementFile(filename=name, kind=kind, source=source, size_bytes=10, text="x")

    hosted = SupplementRetrievalResult(
        doi="x",
        source="europepmc",
        files=[
            _file("methods.pdf", SupplementKind.DOCUMENT, "europepmc"),
            _file("Table_S1.csv", SupplementKind.TABULAR, "europepmc"),
        ],
    )
    linked = SupplementRetrievalResult(
        doi="x",
        source="zenodo",
        files=[_file("samples.xlsx", SupplementKind.TABULAR, "zenodo")],
    )

    merged = merge_results("x", [hosted, linked], [], max_files=2, max_total_bytes=1_000_000)

    assert [f.filename for f in merged.files] == ["Table_S1.csv", "samples.xlsx"]
    assert merged.source == "europepmc+zenodo"


def test_merge_results_keeps_same_basename_from_one_source() -> None:
    # Dedup is on (source, full filename): two distinct paths from one source that
    # happen to share a basename are different files and must both survive.
    from nmdc_metadata_suggestor_ai_tool.models.supplement import (
        SupplementFile,
        SupplementRetrievalResult,
    )

    def _file(name: str) -> SupplementFile:
        return SupplementFile(
            filename=name,
            kind=SupplementKind.TABULAR,
            source="europepmc",
            size_bytes=10,
            text="a,b\n1,2\n",
        )

    result = SupplementRetrievalResult(
        doi="x",
        source="europepmc",
        files=[_file("si/Table_S1.xlsx"), _file("si/extra/Table_S1.xlsx")],
    )

    merged = merge_results("x", [result], [], max_files=10, max_total_bytes=1_000_000)

    assert [f.filename for f in merged.files] == [
        "si/Table_S1.xlsx",
        "si/extra/Table_S1.xlsx",
    ]

    # A true repeat of the same path from the same source is still collapsed.
    repeat = SupplementRetrievalResult(
        doi="x", source="europepmc", files=[_file("si/Table_S1.xlsx")]
    )
    deduped = merge_results("x", [result, repeat], [], max_files=10, max_total_bytes=1_000_000)
    assert len(deduped.files) == 2


def test_merge_results_keeps_user_managed_files(tmp_path: Path) -> None:
    # With save_dir=..., the saved paths are caller-owned outputs: trimming a file
    # from the merged result must not delete it.
    from nmdc_metadata_suggestor_ai_tool.models.supplement import (
        SupplementFile,
        SupplementRetrievalResult,
    )

    save_dir = tmp_path / "supplements"
    save_dir.mkdir()
    dropped_path = save_dir / "dropped.pdf"
    dropped_path.write_bytes(b"%PDF-1.4 dropped")

    def _result(source: str, name: str, path: Path) -> SupplementRetrievalResult:
        return SupplementRetrievalResult(
            doi="x",
            source=source,
            files=[
                SupplementFile(
                    filename=name,
                    kind=SupplementKind.DOCUMENT,
                    source=source,
                    size_bytes=16,
                    saved_path=str(path),
                )
            ],
        )

    merged = merge_results(
        "x",
        [
            _result("europepmc", "kept.pdf", save_dir / "kept.pdf"),
            _result("zenodo", "dropped.pdf", dropped_path),
        ],
        [],
        max_files=1,
        max_total_bytes=1_000_000,
        save_dir=str(save_dir),
    )

    assert [f.filename for f in merged.files] == ["kept.pdf"]
    assert dropped_path.exists()  # caller-managed output left in place


def test_is_removable_temp_file_rejects_paths_outside_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pin the temp root rather than probing the real one: a checkout that itself
    # lives under /tmp would otherwise make every path look removable.
    temp_root = tmp_path / "tmproot"
    temp_root.mkdir()
    outside = tmp_path / "user-outputs"
    outside.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_root))

    assert is_removable_temp_file(str(temp_root / "scratch.pdf")) is True
    assert is_removable_temp_file(str(outside / "table_s1.pdf")) is False


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
# Deselected by default; run with `-m integration`. Each uses a known-good public
# DOI by default (verified to expose downloadable tabular/document files),
# overridable via the matching NMDC_TEST_*_DOI env var:
#   uv run pytest tests/test_supplement_retrieval.py -m integration
# ---------------------------------------------------------------------------

integration = pytest.mark.integration

# Known-good public records with downloadable tabular/document files.
LIVE_ZENODO_DOI = "10.5281/zenodo.8436315"  # .docx + two .csv files
LIVE_FIGSHARE_DOI = "10.6084/m9.figshare.33084674.v1"  # a .pdf file
LIVE_DRYAD_DOI = "10.5061/dryad.51c59zwfj"  # Zenodo-mirrored dataset with .csv files
# Microbiome (BMC), PMC6298009 -- ~1.4 MB supplement ZIP with a captioned PDF.
LIVE_EUROPEPMC_DOI = "10.1186/s40168-018-0605-2"


def _assert_live_result(result: object, source: str) -> None:
    from nmdc_metadata_suggestor_ai_tool.models.supplement import SupplementRetrievalResult

    assert isinstance(result, SupplementRetrievalResult)
    assert result.source == source
    assert result.files, f"expected files, got error: {result.error}"
    for f in result.files:
        assert f.source == source
        assert f.text is not None or f.saved_path is not None
    remove_temp_files([f.saved_path for f in result.files if f.saved_path])


@integration
def test_europepmc_live() -> None:
    # The hosted-supplement path for publication DOIs. Nothing but a live call
    # exercises the real endpoint URL -- mocked tests match whatever URL the code
    # builds, which is how a wrong URL template went unnoticed.
    doi = os.environ.get("NMDC_TEST_EUROPEPMC_DOI", LIVE_EUROPEPMC_DOI)
    _assert_live_result(retrieve_supplements_from_europepmc(doi), "europepmc")


@integration
def test_europepmc_live_url_is_reachable() -> None:
    # Pin the failure to the URL itself: a 404 here means the endpoint shape
    # changed, separate from any parsing/caps behavior downstream.
    doi = os.environ.get("NMDC_TEST_EUROPEPMC_DOI", LIVE_EUROPEPMC_DOI)
    info = find_supplement_source_europepmc(doi)
    assert info.get("error") is None, info
    assert info["has_supplements"] is True, f"expected a record with supplements: {info}"
    assert info["zip_url"], info

    response = request_with_retry("GET", str(info["zip_url"]), stream=True, timeout=DEFAULT_TIMEOUT)
    try:
        assert response.status_code == 200, (
            f"{info['zip_url']} returned {response.status_code}; the endpoint keys on the "
            "bare PMCID and takes no source-DB path segment"
        )
    finally:
        response.close()


@integration
def test_retrieve_supplements_live_publication_doi() -> None:
    # End-to-end through the orchestrator, the way the skill and agent call it.
    doi = os.environ.get("NMDC_TEST_EUROPEPMC_DOI", LIVE_EUROPEPMC_DOI)
    result = retrieve_supplements(doi, follow_related=False)
    assert result.files, f"expected files, got error: {result.error}"
    assert result.pmcid, "expected the Europe PMC record's PMCID to be reported"
    remove_temp_files([f.saved_path for f in result.files if f.saved_path])


@integration
def test_zenodo_live() -> None:
    doi = os.environ.get("NMDC_TEST_ZENODO_DOI", LIVE_ZENODO_DOI)
    _assert_live_result(retrieve_supplements_from_zenodo(doi), "zenodo")


@integration
def test_figshare_live() -> None:
    doi = os.environ.get("NMDC_TEST_FIGSHARE_DOI", LIVE_FIGSHARE_DOI)
    _assert_live_result(retrieve_supplements_from_figshare(doi), "figshare")


@integration
def test_dryad_live() -> None:
    # Dryad's own download API is auth-gated, so content is fetched from the Zenodo
    # mirror. The default DOI is a mirrored dataset; a non-mirrored DOI yields no
    # files (Dryad content is not retrievable unauthenticated).
    doi = os.environ.get("NMDC_TEST_DRYAD_DOI", LIVE_DRYAD_DOI)
    _assert_live_result(retrieve_supplements_from_dryad(doi), "dryad")
