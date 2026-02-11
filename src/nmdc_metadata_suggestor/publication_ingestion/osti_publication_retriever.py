"""
A module to attempt to retrieve publication abstract and PDF bytes via OSTI and ESS Dive.

This module provides direct API-based retrieval of publication abstracts and full-text PDFs from OSIT and ESS Dive. It complements the web scraping approach
by using structured APIs for more reliable access.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import requests

# ============================================================================
# DATA STRUCTURE
# ============================================================================

@dataclass
class PDFResult:
    """Result from PDF retrieval attempt via API.
    
    Attributes:
        success: Whether PDF was successfully retrieved
        content: The PDF binary content
        url: The URL where content was retrieved from
        metadata: Additional info (file size, format, API response details, etc.)
        error: Error message if retrieval failed
    """
    success: bool
    content: Optional[bytes] = None
    url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    abstract: Optional[str] = None
    error: Optional[str] = None


# ============================================================================
# CONSTANTS
# ============================================================================

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)

# API endpoints
ESS_DIVE_API_URL = "https://dive.sdsc.edu/api/v1/publications"
OSTI_API_URL = "https://www.osti.gov/api/v1/records"

# Timeouts
DEFAULT_TIMEOUT = 30
PDF_DOWNLOAD_TIMEOUT = 60



def retrieve_doi_info_from_osti(doi: str) -> Dict[str, Any]:
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
            osti_url
        )
        response.raise_for_status()
        
        data = response.json()
        
        # Check if we got any results
        if not data or len(data) == 0:
            raise ValueError(f"No publication found in OSTI for DOI: {doi}")
        
        # get the record
        record = data[0] if isinstance(data, list) else data
        
        return record
        
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


def save_pdf(result: PDFResult, output_path: str) -> bool:
    """Save PDF content to a file.
    
    Args:
        result: PDFResult containing the PDF content
        output_path: Path where PDF should be saved
        
    Returns:
        True if saved successfully, False otherwise
    """
    if not result.success or not result.content:
        return False
    
    try:
        with open(output_path, "wb") as f:
            f.write(result.content)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    # Example usage
    doi = "10.15485/1729719"
    
    try:
        info = retrieve_doi_info_from_osti(doi)
        print("Publication Information:")
        print(info["description"])
    except Exception as e:
        print(f"Error: {e}")