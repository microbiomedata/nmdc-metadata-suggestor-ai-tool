"""
A module to attempt to retrieve publication abstract and PDF bytes via OSTI and ESS Dive.

This module provides direct API-based retrieval of publication abstracts and full-text PDFs from OSIT and ESS Dive. It complements the web scraping approach
by using structured APIs for more reliable access.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import requests

# ============================================================================
# DATA STRUCTURES
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
OSTI_API_URL = "https://www.osti.gov/api/v1/publications"

# Timeouts
DEFAULT_TIMEOUT = 30
PDF_DOWNLOAD_TIMEOUT = 60


# ============================================================================
# CONVENIENCE FUNCTION
# ============================================================================

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
    dois = ["10.1371/journal.pone.0228165", "10.1073/pnas.2004192118", "10.1038/s41564-020-00861-0", "10.1111/1462-2920.16314", "10.1128/mSystems.00045-18", "10.1038/s41597-024-03069-7", "10.1101/2022.12.12.520098", "10.1016/j.apsoil.2025.106110", "10.1029/2024GL113091", "10.1038/s41564-022-01266-x", "10.1016/j.geoderma.2021.115674", "10.1029/2022JG006889", "10.1002/ppp.2200", "10.1038/s41467-023-36515-y", "10.1021/acs.estlett.0c00748", "10.1128/msystems.00768-19"]