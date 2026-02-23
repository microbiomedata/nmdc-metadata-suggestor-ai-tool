"""
Global constants for the NMDC Metadata Suggestor.

Centralises API endpoints, timeouts, user-agent strings, and other
configuration values that were previously scattered across modules.
Environment-variable overrides are noted inline.
"""

import os
import re

# ---------------------------------------------------------------------------
# Contact / User-Agent
# ---------------------------------------------------------------------------
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "support@microbiomedata.org")
USER_AGENT = f"NMDCMetadataSuggestor/0.1 (mailto:{CONTACT_EMAIL})"

# ---------------------------------------------------------------------------
# HTTP Timeouts (seconds)
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT = 30  # general-purpose API calls (DOI, Crossref, etc.)
LLM_REQUEST_TIMEOUT = 300  # Claude / long-running LLM calls

# ---------------------------------------------------------------------------
# DOI APIs
# ---------------------------------------------------------------------------
DOI_HANDLE_API = "https://doi.org/api/handles"
DOI_RA_API = "https://doi.org/doiRA"
DOI_RESOLVER_URL = "https://doi.org"

# DOI syntax: prefix (10.NNNN+) / suffix (any non-whitespace)
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")

# Handle API success response code
HANDLE_RESPONSE_SUCCESS = 1

# ---------------------------------------------------------------------------
# External Data APIs
# ---------------------------------------------------------------------------
CROSSREF_API_URL = "https://api.crossref.org/works"
DATACITE_API_URL = "https://api.datacite.org/dois"
OPENALEX_API_URL = "https://api.openalex.org/works"

# OSTI
OSTI_API_URL = "https://www.osti.gov/api/v1/records"
OSTI_E2_API_URL = "https://www.osti.gov/elink2api/records"
CROSSREF_V1_API_URL = "https://api.crossref.org/v1/works"

# Europe PMC
EUROPEPMC_API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# PubMed / PMC
PUBMED_ID_CONVERTER = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
PMC_PDF_URL_TEMPLATE = "https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"

# Content negotiation
CITEPROC_JSON_ACCEPT = "application/vnd.citationstyles.csl+json"
