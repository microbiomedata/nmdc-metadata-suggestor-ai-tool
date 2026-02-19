"""
A module to attempt to retrieve publication abstract and PDF bytes via a provided OSTI DOI.

This module provides direct API-based retrieval of OSTI DOIs abstracts
and full-text PDFs links starting from OSTI, then calling Crossref and/or Europe PMC.
"""

from typing import Any

import requests

from nmdc_metadata_suggestor.models.doi import AbstractResult
from nmdc_metadata_suggestor.models.publication import Publication

# API endpoints
OSTI_API_URL = "https://www.osti.gov/api/v1/records"
OSTI_E2_API_URL = "https://www.osti.gov/elink2api/records"
CROSSREF_API_URL = "https://api.crossref.org/v1/works"
EUROPEPMC_API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
# Timeouts
DEFAULT_TIMEOUT = 30


def query_osti_by_doi(osti_doi: str) -> dict:
    """
    Query OSTI API for a given DOI and return the JSON response.
    First queries the new E2 API, and if that fails, falls back to the original API.
    """
    params = {
        "doi": osti_doi,
    }
    headers = {"User-Agent": "NMDC Metadata Suggestor (mailto:support@microbiomedata.org)"}
    try:
        osti_url = f"{OSTI_E2_API_URL}"
        response = requests.get(
            osti_url,
            timeout=DEFAULT_TIMEOUT,
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()  # type: ignore

    except requests.RequestException:
        # Fallback to the original API if the E2 API fails
        osti_url = f"{OSTI_API_URL}"

        response = requests.get(
            osti_url,
            timeout=DEFAULT_TIMEOUT,
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()  # type: ignore


def retrieve_doi_info_from_osti(doi: str) -> AbstractResult:
    """
    Retrieve abstract from OSTI API using a DOI.

    Args:
        doi: Digital Object Identifier (e.g., "10.15485/1729719")

    Returns:
        AbstractResult containing the abstract/description from OSTI.
        If an error occurs, returns AbstractResult with error field populated.
    """
    try:
        data = query_osti_by_doi(osti_doi=doi)
        # check results
        if not data or len(data) == 0:
            return AbstractResult(doi=doi, error=f"No publication found in OSTI for DOI: {doi}")

        # get the record
        record = data[0] if isinstance(data, list) else data
        abstract = record.get("description")

        if abstract:
            return AbstractResult(
                doi=doi,
                abstract=abstract,
                raw_abstract=abstract,
                source="osti",
                content_format="plain_text",
                attempts=["osti"],
            )
        else:
            return AbstractResult(doi=doi, error="No abstract/description found in OSTI record")
    except Exception as e:
        return AbstractResult(doi=doi, error=str(e))


def retrieve_pdf_link_from_osti_doi(doi: str) -> Publication:
    """Retrieve publication information including PDF links from OSTI API using a DOI.

    Args:
        doi: Digital Object Identifier (e.g., "10.15485/1729719")

    Returns:
        Publication object with abstract, URLs, and associated publication DOI.
        If an error occurs, returns Publication with error field populated.
    """
    try:
        data = query_osti_by_doi(doi)

        # Check if we got any results
        if not data or len(data) == 0:
            return Publication(doi=doi, error=f"No publication found in OSTI for DOI: {doi}")

        # get the record
        record = data[0] if isinstance(data, list) else data

        # we can see if the record is a publication or not. If not, return the description.
        if record.get("product_type") == "Journal Article":
            # get the JA DOI
            ja_doi = record.get("doi")
            if not ja_doi:
                return Publication(doi=doi, error="Journal Article record missing DOI")

            crossref_pdf_links = retrieve_pdf_link_from_crossref(ja_doi)

            if len(crossref_pdf_links.get("pdf_links", [])) != 0:
                pub = Publication(
                    source="Crossref",
                    doi=doi,
                    associated_publication_doi=ja_doi,
                    urls=crossref_pdf_links.get("pdf_links"),
                    abstract=record.get("description"),
                )
            else:
                # try pmc
                pmc_pdf_info = retrieve_pdf_link_from_pmc(ja_doi)
                pdf_url = pmc_pdf_info.get("pdf_url")
                pub = Publication(
                    source="PMC" if pdf_url else None,
                    doi=doi,
                    associated_publication_doi=ja_doi,
                    pmid=pmc_pdf_info.get("pmid"),
                    urls=[pdf_url] if pdf_url else None,  # type: ignore[list-item]
                    abstract=record.get("description"),
                )
            return pub
        else:
            return Publication(doi=doi, abstract=record.get("description"))

    except Exception as e:
        return Publication(doi=doi, error=f"Failed to retrieve DOI information from OSTI: {str(e)}")


def retrieve_pdf_link_from_crossref(id: str) -> dict[str, Any]:
    """Get publication metadata from Crossref API.

    Args:
        id: Identifier for the journal or publication

    Returns:
        Dictionary with PDF link or error message
    """
    try:
        response = requests.get(
            f"{CROSSREF_API_URL}/{id}",
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": "NMDC Metadata Suggestor (mailto:support@microbiomedata.org)"},
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
        response = requests.get(EUROPEPMC_API_URL, params=params, timeout=DEFAULT_TIMEOUT)  # type: ignore
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
                pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"

            return {"pdf_url": pdf_url, "pmcid": article.get("pmcid")}

        return {"pdf_url": None, "pmcid": None, "error": "No results found"}

    except Exception as e:
        return {"pdf_url": None, "pmcid": None, "error": str(e)}
