from typing import Any

from nmdc_metadata_suggestor.constants import (
    CROSSREF_API_URL,
    DEFAULT_TIMEOUT,
    EUROPEPMC_API_URL,
    PMC_PDF_URL_TEMPLATE,
    USER_AGENT,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import request_with_retry


def retrieve_pdf_link_from_crossref(id: str) -> dict[str, Any]:
    """Get publication metadata from Crossref API.

    Args:
        id: Identifier for the journal or publication

    Returns:
        Dictionary with PDF link or error message
    """
    try:
        response = request_with_retry(
            "GET",
            f"{CROSSREF_API_URL}/{id}",
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        link_data = response.json()["message"]["link"]
        # collect all PDF links
        pdf_links = []
        for link in link_data:
            if link.get("content-type") == "application/pdf":
                pdf_links.append(link.get("URL"))
        return {"pdf_links": pdf_links}
    except Exception as e:
        return {"error": str(e), "pdf_links": []}


def retrieve_pdf_link_from_pmc(doi: str) -> dict[str, Any]:
    """Get publication info and PDF from Europe PMC.

    Args:
        doi: Digital Object Identifier

    Returns:
        Dict with pdf_url, pmcid, and optional error keys
    """
    try:
        params = {"query": f'DOI:"{doi}"', "format": "json", "pageSize": 1}
        response = request_with_retry(
            "GET",
            EUROPEPMC_API_URL,
            params=params,
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        data = response.json()

        if data.get("resultList", {}).get("result"):
            article = data["resultList"]["result"][0]
            pdf_url = None

            # Check for PDF links in the article metadata from Europe PMC
            if article.get("isOpenAccess") == "Y" and article.get("fullTextUrlList"):
                for url_entry in article["fullTextUrlList"].get("fullTextUrl", []):
                    if url_entry.get("documentStyle") == "pdf":
                        pdf_url = url_entry.get("url")
                        break

            # If no PDF link found, try PMC ID for PDF
            if not pdf_url and article.get("pmcid"):
                pmcid = article["pmcid"]
                pdf_url = PMC_PDF_URL_TEMPLATE.format(pmcid=pmcid)

            return {"pdf_url": pdf_url, "pmcid": article.get("pmcid")}

        return {"pdf_url": None, "pmcid": None, "error": "No results found"}

    except Exception as e:
        return {"pdf_url": None, "pmcid": None, "error": str(e)}
