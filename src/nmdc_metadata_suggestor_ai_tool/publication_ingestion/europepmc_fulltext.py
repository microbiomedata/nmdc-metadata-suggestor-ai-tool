"""Retrieve full text (JATS XML and PDF) from Europe PMC.

Europe PMC provides free, keyless access to open-access full text for
articles deposited in PubMed Central.  All PMC OA content is CC-licensed
with no generative-AI restrictions.

Programmatic usage::

    from nmdc_metadata_suggestor_ai_tool.publication_ingestion.europepmc_fulltext import (
        get_full_text,
        fetch_pdf_bytes,
    )

    result = get_full_text("10.1038/s41564-020-00861-0")
    if result.full_text_xml:
        print(f"Got {len(result.full_text_xml)} chars of JATS XML")

    # PDF bytes are fetched separately (large binary):
    if result.pmcid:
        pdf = fetch_pdf_bytes(result.pmcid)

CLI usage::

    make get-fulltext DOI=10.1038/s41564-020-00861-0
"""

from __future__ import annotations

import logging

import requests

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_TIMEOUT,
    EUROPEPMC_API_URL,
    EUROPEPMC_FULLTEXT_XML_URL,
    EUROPEPMC_PDF_URL,
    USER_AGENT,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    normalize_doi,
    request_with_retry,
)
from nmdc_metadata_suggestor_ai_tool.models.doi import FullTextRetrievalResult

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": USER_AGENT}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def resolve_pmcid(
    doi: str, errors: list[str] | None = None
) -> tuple[str | None, bool | None]:
    """Look up a PMCID and open-access flag for *doi* via Europe PMC search.

    Returns ``(pmcid, is_open_access)`` on success, or ``(None, None)`` if the
    DOI is not in PubMed Central.  Errors are appended to *errors* if provided.
    """
    try:
        params = {"query": f'DOI:"{doi}"', "format": "json", "pageSize": 1}
        resp = request_with_retry(
            "GET",
            EUROPEPMC_API_URL,
            params=params,
            timeout=DEFAULT_TIMEOUT,
            headers=_HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()

        results = data.get("resultList", {}).get("result", [])
        if not results:
            return None, None

        article = results[0]
        pmcid = article.get("pmcid")
        is_oa = article.get("isOpenAccess") == "Y"
        return pmcid, is_oa

    except Exception as exc:
        msg = f"Europe PMC search failed: {exc}"
        logger.warning(msg)
        if errors is not None:
            errors.append(msg)
        return None, None


def fetch_fulltext_xml(pmcid: str) -> str | None:
    """Fetch JATS XML full text for *pmcid* from Europe PMC.

    Returns the raw XML string, or ``None`` on 404 / empty response.
    """
    url = EUROPEPMC_FULLTEXT_XML_URL.format(pmcid=pmcid)
    try:
        resp = request_with_retry(
            "GET", url, timeout=DEFAULT_TIMEOUT, headers=_HEADERS
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        text = resp.text
        return text if text.strip() else None
    except requests.exceptions.HTTPError:
        return None
    except Exception as exc:
        logger.warning("Failed to fetch full text XML for %s: %s", pmcid, exc)
        return None


def fetch_pdf_bytes(pmcid: str) -> bytes | None:
    """Fetch PDF binary for *pmcid* from Europe PMC.

    Checks that the response Content-Type contains ``application/pdf`` to
    reject HTML error pages.  Returns raw bytes, or ``None``.
    """
    url = EUROPEPMC_PDF_URL.format(pmcid=pmcid)
    try:
        resp = request_with_retry(
            "GET", url, timeout=DEFAULT_TIMEOUT, headers=_HEADERS
        )
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "application/pdf" not in content_type:
            logger.warning(
                "Expected PDF but got Content-Type=%s for %s", content_type, pmcid
            )
            return None
        return resp.content
    except Exception as exc:
        logger.warning("Failed to fetch PDF for %s: %s", pmcid, exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_full_text(doi: str) -> FullTextRetrievalResult:
    """Retrieve full text for *doi* from Europe PMC.

    Steps:
      1. Resolve DOI → PMCID via Europe PMC search API.
      2. Fetch JATS XML full text for the PMCID.
      3. Construct the Europe PMC PDF URL (bytes are NOT downloaded).

    Callers that need PDF bytes should call ``fetch_pdf_bytes(result.pmcid)``
    separately.
    """
    doi = normalize_doi(doi)
    attempts: list[str] = []
    errors: list[str] = []

    # Step 1: resolve PMCID
    attempts.append("europepmc_search")
    pmcid, is_oa = resolve_pmcid(doi, errors=errors)

    if not pmcid:
        return FullTextRetrievalResult(
            doi=doi,
            attempts=attempts,
            error=f"DOI {doi} not found in PubMed Central",
        )

    # Step 2: fetch JATS XML
    attempts.append("europepmc_fulltext_xml")
    xml = fetch_fulltext_xml(pmcid)

    # Step 3: construct PDF URL (don't download)
    pdf_url = EUROPEPMC_PDF_URL.format(pmcid=pmcid)

    error = None
    if not xml:
        error = f"PMCID {pmcid} found but full text XML not available"

    return FullTextRetrievalResult(
        doi=doi,
        pmcid=pmcid,
        is_open_access=is_oa,
        source="europepmc",
        attempts=attempts,
        full_text_xml=xml,
        pdf_url=pdf_url,
        error=error,
    )
