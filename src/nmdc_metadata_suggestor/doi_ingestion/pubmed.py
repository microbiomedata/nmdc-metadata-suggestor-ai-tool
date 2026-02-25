import requests
from nmdc_metadata_suggestor.constants import (
    PUBMED_EFETCH,
    PUBMED_ID_CONVERTER,
    DEFAULT_TIMEOUT,
    USER_AGENT,
)

from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    request_with_retry,
)
from nmdc_metadata_suggestor.doi_ingestion.resolver_context import ResolverContext

def try_pubmed(doi: str) -> ResolverContext:
    """Fetch abstract from PubMed via DOI -> PMID -> efetch.

    Returns:
        ResolverContext with the abstract text and PMID, or None if PubMed has no record.
    """
    # Step 1: DOI -> PMID via ID converter
    try:
        response = request_with_retry(
            "GET",
            PUBMED_ID_CONVERTER,
            params={"ids": doi, "format": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return ResolverContext(doi=doi, error="Failed to fetch PMID")
        data = response.json()
        records = data.get("records", [])
        if not records:
            return ResolverContext(doi=doi, error="No PMID found")
        pmid = records[0].get("pmid")
        if not pmid or pmid == "0":
            return ResolverContext(doi=doi, error="Invalid PMID")
    except (requests.RequestException, ValueError):
        return ResolverContext(doi=doi, error="Error fetching PMID")

    # Step 2: PMID -> abstract via efetch
    try:
        response = request_with_retry(
            "GET",
            PUBMED_EFETCH,
            params={"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "text"},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return ResolverContext(doi=doi, error="Failed to fetch abstract", pmid=pmid)
        text = response.text.strip()
        if text:
            return ResolverContext(doi=doi, abstract=text, pmid=pmid)
        return ResolverContext(doi=doi, error="No abstract found", pmid=pmid)
    except requests.RequestException:
        return ResolverContext(doi=doi, error="Error fetching abstract", pmid=pmid)