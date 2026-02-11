"""
A module to attempt to retrieve publication abstract and PDF bytes via OSTI and ESS Dive.

This module provides direct API-based retrieval of publication abstracts and full-text PDFs from OSIT and ESS Dive. It complements the web scraping approach
by using structured APIs for more reliable access.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import requests


# API endpoints
OSTI_API_URL = "https://www.osti.gov/api/v1/records"



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
        # we can see if the record is a publication or not. If not, we can just return the description.
        if record["product_type"] == "Journal Article":
            # gather the publisher info
            publisher = record.get("publisher", "Unknown Publisher")
            # check if the publisher is one of the supported ones using fuzzy matching
            # This allows "Springer Science + Business Media" to match "Springer Nature"
            supported_publishers = ["ASLO", "Nature", "Frontiers", "Elsevier", "Soil Science Society", "CyVerse", "Springer"]
            
            # Case-insensitive partial matching
            publisher_lower = publisher.lower()
            is_supported = any(
                supported.lower() in publisher_lower 
                for supported in supported_publishers
            )
            if not is_supported:
                return record["description"]
            else:
                # call marks code as needed 
                pass
        else:
            return record["description"]
        
        
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


if __name__ == "__main__":
    # example 
    doi = ["10.15485/1729719", "10.15485/1603775"]
    
    info = retrieve_doi_info_from_osti(doi[0])
    print(info)