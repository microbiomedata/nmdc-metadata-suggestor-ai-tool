"""
Global constants for the NMDC Metadata Suggestor.

Centralises API endpoints, timeouts, user-agent strings, and other
configuration values that were previously scattered across modules.
Environment-variable overrides are noted inline.
"""

import os
import re

# ---------------------------------------------------------------------------
# Supported sources for abstract and publication retrieval
# ---------------------------------------------------------------------------
ALL_SOURCES = (
    "openalex", "crossref", "elsevier", "springer_nature",
    "pubmed", "content_negotiation", "osti",
)

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

# Elsevier ScienceDirect — requires API key from https://dev.elsevier.com/
# Production deployments should use an institutional or team API key,
# not a personal developer key (personal keys are tied to individual accounts).
# The API key is read at call time via os.environ.get("ELSEVIER_API_KEY")
# in doi_ingestion/elsevier.py (not captured here, to avoid import-time issues).
ELSEVIER_API_URL = "https://api.elsevier.com/content/article/doi"

# Springer Nature Metadata API — requires API key from https://dev.springernature.com/
# Free registration; no institutional affiliation required.
# The API key is read at call time via os.environ.get("SPRINGER_NATURE_API_KEY")
# in doi_ingestion/springer_nature.py (not captured here, to avoid import-time issues).
SPRINGER_NATURE_API_URL = "https://api.springernature.com/metadata/json"

DATACITE_API_URL = "https://api.datacite.org/dois"
OPENALEX_API_URL = "https://api.openalex.org/works"

# OSTI
OSTI_API_URL = "https://www.osti.gov/api/v1/records"
OSTI_E2_API_URL = "https://www.osti.gov/elink2api/records"

# Europe PMC
EUROPEPMC_API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# PubMed / PMC
PUBMED_ID_CONVERTER = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
PMC_PDF_URL_TEMPLATE = "https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"

# Content negotiation
CITEPROC_JSON_ACCEPT = "application/vnd.citationstyles.csl+json"

# ---------------------------------------------------------------------------
# DOI context resolver APIs
# ---------------------------------------------------------------------------
EDI_DOI_API = "https://pasta.lternet.edu/package/doi"
EMSL_PROJECTS_API = "https://api.emsl.pnnl.gov/external/projects"
ESS_DIVE_API = "https://api.ess-dive.lbl.gov/packages"
DATAONE_CN_SOLR_API = "https://cn.dataone.org/cn/v2/query/solr/"
FIGSHARE_API = "https://api.figshare.com/v2/articles"
FIGSHARE_COLLECTIONS_API = "https://api.figshare.com/v2/collections"
JGI_SEARCH_API = "https://files.jgi.doe.gov/search/"
KBASE_SEARCH_API = "https://kbase.us/services/searchapi2/rpc"
KBASE_WORKSPACE_API = "https://kbase.us/services/ws"
DOI_CONTENT_NEGOTIATION_API = DOI_RESOLVER_URL
PROXI_DATASETS_API = "https://proteomecentral.proteomexchange.org/api/proxi/v0.1/datasets"
CYVERSE_METADATA_API = "https://de.cyverse.org/terrain/filesystem/metadata"
CYVERSE_METADATA_SEARCH_API = f"{CYVERSE_METADATA_API}/search"
ZENODO_API = "https://zenodo.org/api/records"

# Safety limits for untrusted XML payloads in DOI resolvers.
MAX_EDI_METADATA_XML_CHARS = int(os.environ.get("NMDC_EDI_MAX_XML_CHARS", "2000000"))
MAX_DATAONE_SOLR_XML_CHARS = int(os.environ.get("NMDC_DATAONE_SOLR_MAX_XML_CHARS", "2000000"))
UNSAFE_XML_DECLARATION_PATTERN = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
