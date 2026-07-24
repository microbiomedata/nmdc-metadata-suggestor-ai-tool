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
ALL_SOURCES = ("openalex", "crossref", "pubmed", "content_negotiation", "osti")

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

# Europe PMC
EUROPEPMC_REST_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
EUROPEPMC_API_URL = f"{EUROPEPMC_REST_URL}/search"
# Supplementary files endpoint: returns a ZIP archive of an article's
# supplements. ``source`` is the Europe PMC source DB (e.g. ``PMC``) and
# ``article_id`` is that source's article id (e.g. ``PMC3258517``).
EUROPEPMC_SUPPL_URL_TEMPLATE = f"{EUROPEPMC_REST_URL}/{{source}}/{{article_id}}/supplementaryFiles"
# Full-text JATS XML endpoint. Its ``<supplementary-material>`` elements carry
# captions/labels used to select supplements by content rather than extension.
EUROPEPMC_FULLTEXT_XML_URL_TEMPLATE = f"{EUROPEPMC_REST_URL}/{{source}}/{{article_id}}/fullTextXML"

# NCBI PMC Open Access Web Service: resolves a PMCID to a downloadable OA
# package (``.tar.gz``) containing the full text plus all supplementary files.
PMC_OA_SERVICE_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"

# Dryad data repository (hosts supplement-as-dataset files; DOI prefix 10.5061).
DRYAD_API_URL = "https://datadryad.org/api/v2"
DRYAD_DOI_PREFIX = "10.5061"

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
CYVERSE_DATACOMMONS_API = "https://datacommons.cyverse.org/angular/reverse/"
CYVERSE_DATACOMMONS_BROWSE_HOST = "datacommons.cyverse.org"
ZENODO_API = "https://zenodo.org/api/records"

# Safety limits for untrusted XML payloads in DOI resolvers.
MAX_EDI_METADATA_XML_CHARS = int(os.environ.get("NMDC_EDI_MAX_XML_CHARS", "2000000"))
MAX_DATAONE_SOLR_XML_CHARS = int(os.environ.get("NMDC_DATAONE_SOLR_MAX_XML_CHARS", "2000000"))
UNSAFE_XML_DECLARATION_PATTERN = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


# DOI prefixes that indicate the target provider for context retrieval.
TARGET_PROVIDER_PREFIXES: dict[str, str] = {
    "10.6073": "edi",
    "10.46936": "emsl",
    "10.15485": "ess-dive",
    "10.21952": "ess-dive",
    "10.6084": "figshare",
    "10.25585": "jgi",
    "10.25982": "kbase",
    "10.25345": "massive",
    "10.17504": "cyverse",
    "10.5281": "zenodo",
    "10.11578": "osti",
}

TARGET_PROVIDER_KEYWORDS: dict[str, str] = {
    "environmental data initiative": "edi",
    "emsl": "emsl",
    "ess-dive": "ess-dive",
    "figshare": "figshare",
    "genomic standards consortium": "gsc",
    "jgi": "jgi",
    "kbase": "kbase",
    "massive": "massive",
    "osti": "osti",
    "office of scientific and technical information": "osti",
    "zenodo": "zenodo",
    "cyverse": "cyverse",
}

# ---------------------------------------------------------------------------
# Supplementary material retrieval
#
# Performance-first caps: supplement retrieval favors speed over completeness,
# so downloads are bounded and never retried beyond ``request_with_retry``'s
# transient-error handling. Override via environment variables if needed.
# ---------------------------------------------------------------------------
# Max number of supplement files kept from a single article.
SUPPLEMENT_MAX_FILES = int(os.environ.get("NMDC_SUPPLEMENT_MAX_FILES", "10"))
# Max size of a single supplement file (bytes); larger files are skipped.
SUPPLEMENT_MAX_FILE_BYTES = int(os.environ.get("NMDC_SUPPLEMENT_MAX_FILE_BYTES", str(10_000_000)))
# Max combined size of all kept supplement files (bytes).
SUPPLEMENT_MAX_TOTAL_BYTES = int(os.environ.get("NMDC_SUPPLEMENT_MAX_TOTAL_BYTES", str(50_000_000)))
# Max size of the supplement ZIP archive to download (bytes); larger archives
# are skipped without downloading (checked against the Content-Length header).
SUPPLEMENT_MAX_ARCHIVE_BYTES = int(
    os.environ.get("NMDC_SUPPLEMENT_MAX_ARCHIVE_BYTES", str(100_000_000))
)
# Max characters of decoded text inlined per text-like supplement file.
SUPPLEMENT_MAX_TEXT_CHARS = int(os.environ.get("NMDC_SUPPLEMENT_MAX_TEXT_CHARS", str(200_000)))
# Max size of a full-text JATS XML document fetched for supplement captions.
# Captions are a nice-to-have, so an over-large document is skipped rather than
# fully buffered/parsed (matches the bounded handling of untrusted XML elsewhere).
SUPPLEMENT_MAX_XML_BYTES = int(os.environ.get("NMDC_SUPPLEMENT_MAX_XML_BYTES", str(20_000_000)))

# ---------------------------------------------------------------------------
# Schema context builder
# ---------------------------------------------------------------------------
INTERFACE_CLASS_SUFFIX = "Interface"
EXCLUDED_INTERFACE_CLASSES: frozenset[str] = frozenset(
    {"DhInterface", "JgiMgInterface", "JgiMgLrInterface", "JgiMtInterface", "EmslInterface"}
)
EXCLUDED_SLOTS: frozenset[str] = frozenset(
    {
        "ecosystem",
        "ecosystem_category",
        "ecosystem_type",
        "ecosystem_subtype",
        "specific_ecosystem",
        "collection_date_inc",
    }
)
