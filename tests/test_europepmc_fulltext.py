"""Tests for Europe PMC full text retrieval.

Unit tests mock HTTP with ``responses``; integration tests hit real APIs.
"""

import pytest
import responses

from nmdc_metadata_suggestor_ai_tool.constants import (
    EUROPEPMC_API_URL,
    EUROPEPMC_FULLTEXT_XML_URL,
    EUROPEPMC_PDF_URL,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.europepmc_fulltext import (
    fetch_fulltext_xml,
    fetch_pdf_bytes,
    get_full_text,
    resolve_pmcid,
)

integration = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_PMCID = "PMC7847483"
SAMPLE_DOI = "10.1038/s41564-020-00861-0"

SAMPLE_EUROPEPMC_RESPONSE = {
    "resultList": {
        "result": [
            {
                "doi": SAMPLE_DOI,
                "pmcid": SAMPLE_PMCID,
                "isOpenAccess": "Y",
                "title": "A genomic catalog of Earth's microbiomes",
            }
        ]
    }
}

SAMPLE_JATS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<article>
  <front>
    <article-meta>
      <article-id pub-id-type="doi">10.1038/s41564-020-00861-0</article-id>
      <title-group>
        <article-title>A genomic catalog of Earth's microbiomes</article-title>
      </title-group>
    </article-meta>
  </front>
  <body><p>Sample body text.</p></body>
</article>
"""

SAMPLE_PDF_BYTES = b"%PDF-1.4 fake pdf content for testing"


# ---------------------------------------------------------------------------
# Unit tests — resolve_pmcid
# ---------------------------------------------------------------------------


@responses.activate
def test_resolve_pmcid_success():
    """DOI found in Europe PMC with PMCID and OA flag."""
    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json=SAMPLE_EUROPEPMC_RESPONSE,
        status=200,
    )
    pmcid, is_oa = resolve_pmcid(SAMPLE_DOI)
    assert pmcid == SAMPLE_PMCID
    assert is_oa is True


@responses.activate
def test_resolve_pmcid_not_in_pmc():
    """DOI found in Europe PMC but no PMCID (not deposited in PMC)."""
    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json={
            "resultList": {
                "result": [
                    {
                        "doi": "10.1234/no-pmc",
                        "isOpenAccess": "N",
                        # No pmcid field
                    }
                ]
            }
        },
        status=200,
    )
    pmcid, is_oa = resolve_pmcid("10.1234/no-pmc")
    assert pmcid is None
    assert is_oa is False


@responses.activate
def test_resolve_pmcid_no_results():
    """DOI not found in Europe PMC at all."""
    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json={"resultList": {"result": []}},
        status=200,
    )
    pmcid, is_oa = resolve_pmcid("10.9999/does-not-exist")
    assert pmcid is None
    assert is_oa is None


# ---------------------------------------------------------------------------
# Unit tests — fetch_fulltext_xml
# ---------------------------------------------------------------------------


@responses.activate
def test_fetch_fulltext_xml_success():
    """Returns JATS XML string for valid PMCID."""
    url = EUROPEPMC_FULLTEXT_XML_URL.format(pmcid=SAMPLE_PMCID)
    responses.add(responses.GET, url, body=SAMPLE_JATS_XML, status=200)
    xml = fetch_fulltext_xml(SAMPLE_PMCID)
    assert xml is not None
    assert "<article>" in xml
    assert "10.1038/s41564-020-00861-0" in xml


@responses.activate
def test_fetch_fulltext_xml_404():
    """Returns None on 404."""
    url = EUROPEPMC_FULLTEXT_XML_URL.format(pmcid="PMC0000000")
    responses.add(responses.GET, url, status=404)
    xml = fetch_fulltext_xml("PMC0000000")
    assert xml is None


# ---------------------------------------------------------------------------
# Unit tests — fetch_pdf_bytes
# ---------------------------------------------------------------------------


@responses.activate
def test_fetch_pdf_bytes_success():
    """Returns bytes with PDF magic header."""
    url = EUROPEPMC_PDF_URL.format(pmcid=SAMPLE_PMCID)
    responses.add(
        responses.GET,
        url,
        body=SAMPLE_PDF_BYTES,
        status=200,
        content_type="application/pdf",
    )
    result = fetch_pdf_bytes(SAMPLE_PMCID)
    assert result is not None
    assert result.startswith(b"%PDF-")


@responses.activate
def test_fetch_pdf_bytes_html_response():
    """Rejects HTML content-type (error page) → returns None."""
    url = EUROPEPMC_PDF_URL.format(pmcid="PMC0000000")
    responses.add(
        responses.GET,
        url,
        body=b"<html><body>Error</body></html>",
        status=200,
        content_type="text/html",
    )
    result = fetch_pdf_bytes("PMC0000000")
    assert result is None


# ---------------------------------------------------------------------------
# Unit tests — get_full_text (end-to-end with mocks)
# ---------------------------------------------------------------------------


@responses.activate
def test_get_full_text_happy_path():
    """Full chain: PMCID + XML + PDF URL, no error."""
    # Mock Europe PMC search
    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json=SAMPLE_EUROPEPMC_RESPONSE,
        status=200,
    )
    # Mock fulltext XML
    xml_url = EUROPEPMC_FULLTEXT_XML_URL.format(pmcid=SAMPLE_PMCID)
    responses.add(responses.GET, xml_url, body=SAMPLE_JATS_XML, status=200)

    result = get_full_text(SAMPLE_DOI)

    assert result.doi == SAMPLE_DOI
    assert result.pmcid == SAMPLE_PMCID
    assert result.is_open_access is True
    assert result.source == "europepmc"
    assert result.error is None
    assert result.full_text_xml is not None
    assert "<article>" in result.full_text_xml
    assert result.pdf_url is not None
    assert SAMPLE_PMCID in result.pdf_url


@responses.activate
def test_get_full_text_no_pmcid():
    """DOI not in PMC → error message."""
    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json={"resultList": {"result": []}},
        status=200,
    )

    result = get_full_text("10.9999/not-in-pmc")

    assert result.error is not None
    assert "not found in PubMed Central" in result.error
    assert result.pmcid is None
    assert result.full_text_xml is None
    assert result.pdf_url is None


@responses.activate
def test_get_full_text_xml_unavailable():
    """PMCID found but XML 404 → pdf_url still set, error reported."""
    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json=SAMPLE_EUROPEPMC_RESPONSE,
        status=200,
    )
    xml_url = EUROPEPMC_FULLTEXT_XML_URL.format(pmcid=SAMPLE_PMCID)
    responses.add(responses.GET, xml_url, status=404)

    result = get_full_text(SAMPLE_DOI)

    assert result.pmcid == SAMPLE_PMCID
    assert result.full_text_xml is None
    assert result.pdf_url is not None  # Still constructed from PMCID
    assert result.error is not None
    assert "not available" in result.error


@responses.activate
def test_attempts_tracking():
    """Attempts list records each step tried."""
    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json=SAMPLE_EUROPEPMC_RESPONSE,
        status=200,
    )
    xml_url = EUROPEPMC_FULLTEXT_XML_URL.format(pmcid=SAMPLE_PMCID)
    responses.add(responses.GET, xml_url, body=SAMPLE_JATS_XML, status=200)

    result = get_full_text(SAMPLE_DOI)

    assert "europepmc_search" in result.attempts
    assert "europepmc_fulltext_xml" in result.attempts
    assert len(result.attempts) == 2


@responses.activate
def test_get_full_text_normalizes_doi():
    """DOI URL form is normalized to bare DOI."""
    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json=SAMPLE_EUROPEPMC_RESPONSE,
        status=200,
    )
    xml_url = EUROPEPMC_FULLTEXT_XML_URL.format(pmcid=SAMPLE_PMCID)
    responses.add(responses.GET, xml_url, body=SAMPLE_JATS_XML, status=200)

    result = get_full_text(f"https://doi.org/{SAMPLE_DOI}")

    assert result.doi == SAMPLE_DOI


@responses.activate
def test_pdf_bytes_excluded_from_serialization():
    """pdf_bytes is excluded from model_dump() output."""
    responses.add(
        responses.GET,
        EUROPEPMC_API_URL,
        json=SAMPLE_EUROPEPMC_RESPONSE,
        status=200,
    )
    xml_url = EUROPEPMC_FULLTEXT_XML_URL.format(pmcid=SAMPLE_PMCID)
    responses.add(responses.GET, xml_url, body=SAMPLE_JATS_XML, status=200)

    result = get_full_text(SAMPLE_DOI)
    # Manually set pdf_bytes to verify it's excluded
    result.pdf_bytes = SAMPLE_PDF_BYTES
    dump = result.model_dump()

    assert "pdf_bytes" not in dump
    assert result.pdf_bytes == SAMPLE_PDF_BYTES  # Still accessible on the object


# ---------------------------------------------------------------------------
# Integration tests — hit real Europe PMC APIs
# ---------------------------------------------------------------------------


@integration
def test_get_full_text_real_nature_doi():
    """Nature article in PMC OA — full XML expected."""
    result = get_full_text("10.1038/s41564-020-00861-0")

    assert result.pmcid is not None
    assert result.is_open_access is True
    assert result.source == "europepmc"
    assert result.full_text_xml is not None
    assert len(result.full_text_xml) > 1000
    assert result.pdf_url is not None
    assert result.error is None


@integration
def test_get_full_text_real_plos_doi():
    """PLOS ONE OA article — should have full text."""
    result = get_full_text("10.1371/journal.pone.0252128")

    assert result.pmcid is not None
    assert result.full_text_xml is not None
    assert len(result.full_text_xml) > 1000
    assert result.pdf_url is not None
    assert result.error is None


@integration
def test_get_full_text_not_in_pmc():
    """Paywalled / non-PMC DOI — graceful failure."""
    # This Elsevier DOI is typically not in PMC
    result = get_full_text("10.1016/j.cell.2009.01.043")

    # Should either not find PMCID or not find XML
    # The key thing is no crash and a clear error
    if result.pmcid is None:
        assert "not found in PubMed Central" in result.error
    else:
        # Some Elsevier articles are in PMC; if so, that's fine
        assert result.source == "europepmc"


@integration
def test_fetch_pdf_bytes_real():
    """Verify PDF bytes from Europe PMC have %PDF- header."""
    # First resolve a known PMCID
    pmcid, _ = resolve_pmcid("10.1038/s41564-020-00861-0")
    assert pmcid is not None

    pdf = fetch_pdf_bytes(pmcid)
    assert pdf is not None
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 10000  # Should be a substantial PDF
