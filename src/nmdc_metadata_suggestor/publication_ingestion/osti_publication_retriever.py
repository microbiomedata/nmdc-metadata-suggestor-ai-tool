"""
A module to attempt to retrieve publication abstract and PDF bytes via a provided OSTI DOI.

This module provides direct API-based retrieval of publication abstracts and full-text PDFs starting from OSTI, then calling Crossref and/or Europe PMC.
"""

from typing import Any, Dict
import requests
from nmdc_metadata_suggestor.models.publication import Publication


# API endpoints
OSTI_API_URL = "https://www.osti.gov/api/v1/records"
CROSSREF_API_URL = "https://api.crossref.org/v1/works"
EUROPEPMC_API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# Timeouts
DEFAULT_TIMEOUT = 30


def retrieve_doi_info_from_osti(doi: str) -> Publication:
    """Retrieve publication information from OSTI API using a DOI.
    
    Args:
        doi: Digital Object Identifier (e.g., "10.15485/1729719")
        
    Returns:
        Dictionary containing publication information. Response fields from here https://www.osti.gov/api/v1/docs. 
        
    Raises:
        requests.exceptions.RequestException: If API request fails
    """

    # strip to just the Osti ID
    osti_id = doi.split("/")[-1]
    osti_url = f"{OSTI_API_URL}/{osti_id}"
    
    try:

        response = requests.get(
            osti_url,
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": "NMDC Metadata Suggestor (mailto:support@microbiomedata.org)"}
        )
        response.raise_for_status()
        
        data = response.json()
        
        # Check if we got any results
        if not data or len(data) == 0:
            raise ValueError(f"No publication found in OSTI for DOI: {doi}")
        
        # get the record
        record = data[0] if isinstance(data, list) else data
        # we can see if the record is a publication or not. If not, we can just return the description.
        if record["product_type"] == "Journal Article":
            # get the JA DOI
            ja_doi = record.get("doi")
            crossef_pdf_links = retrieve_pdf_link_from_crossref(ja_doi)
            if len(crossef_pdf_links.get("pdf_links")) != 0:
                pub = Publication(
                    source = "Crossref",
                    osti_doi=doi,
                    publication_doi=ja_doi,
                    urls=crossef_pdf_links.get("pdf_links"),
                    abstract=record.get("description")
                )
                
            else:
                # try pmc
                pmc_pdf_info = retrieve_pdf_link_from_pmc(ja_doi)
                pub = Publication(
                    source = "PMC" if pmc_pdf_info.get("pdf_url") else None,
                    osti_doi=doi,
                    publication_doi=ja_doi,
                    pmid=pmc_pdf_info.get("pmid"),
                    urls=record.get("urls"),
                    abstract=record.get("description")
                )
            return pub
        else:
            return Publication(
                osti_doi=doi,
                abstract=record.get("description")
            )
        
        
    except requests.exceptions.Timeout:
        raise requests.exceptions.RequestException(
            f"OSTI API request timed out for DOI: {doi}"
        )
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise ValueError(f"DOI not found in OSTI: {doi}")
        raise requests.exceptions.RequestException(
            f"OSTI API request failed with status {e.response.status_code}: {str(e)}"
        )
    except Exception as e:
        raise requests.exceptions.RequestException(
            f"Failed to retrieve DOI information from OSTI: {str(e)}"
        )
    
def retrieve_pdf_link_from_crossref(id: str) -> Dict[str, Any]:
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
            headers={"User-Agent": "NMDC Metadata Suggestor (mailto:support@microbiomedata.org)"}
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


def retrieve_pdf_link_from_pmc(doi: str) -> Dict[str, Any]:
    """Get publication info and PDF from Europe PMC.
    
    Args:
        doi: Digital Object Identifier
        
    Returns:
        Dict including PDF URL if available and pmcid
    """
    try:
        params = {
            "query": f'DOI:"{doi}"',
            "format": "json",
            "pageSize": 1
        }
        response = requests.get(
            EUROPEPMC_API_URL,
            params=params,
            timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get("resultList", {}).get("result"):
            article = data["resultList"]["result"][0]
            pdf_url = None
            
            # Check for PDF
            if article.get("isOpenAccess") == "Y" and article.get("fullTextUrlList"):
                for url_entry in article["fullTextUrlList"].get("fullTextUrl", []):
                    if url_entry.get("documentStyle") == "pdf":
                        pdf_url = url_entry.get("url")
                        break
            
            # Try PMC ID for PDF
            if not pdf_url and article.get("pmcid"):
                pmcid = article["pmcid"]
                pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
            
            return {"pdf_url": pdf_url, "pmcid": article.get("pmcid")}
        
        return {"error": "No results found"}
        
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    doi = ["10.15485/2478895", "10.15485/1729719", "10.15485/1603775"]
    info = retrieve_doi_info_from_osti(doi[0])
    print(info)