import requests

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_TIMEOUT,
    PUBMED_EFETCH,
    PUBMED_ID_CONVERTER,
    USER_AGENT,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    append_error,
    request_with_retry,
)
from nmdc_metadata_suggestor_ai_tool.models.resolver_context import ResolverContext


def try_pubmed(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Fetch abstract from PubMed via DOI -> PMID -> efetch.

    Returns:
        ResolverContext with abstract text and PMID metadata, or None if no PubMed record.
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
            append_error(errors, f"PubMed ID converter returned HTTP {response.status_code}")
            return None
        data = response.json()
        records = data.get("records", [])
        if not records:
            append_error(errors, "PubMed ID converter found no PMID for DOI")
            return None
        pmid = records[0].get("pmid")
        if not pmid or pmid == "0":
            append_error(errors, "PubMed ID converter returned invalid PMID")
            return None
    except (requests.RequestException, ValueError) as exc:
        append_error(errors, f"PubMed ID converter request failed: {exc.__class__.__name__}")
        return None

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
            append_error(errors, f"PubMed efetch returned HTTP {response.status_code}")
            return None
        text = response.text.strip()
        if text:
            return ResolverContext(
                text=text,
                raw_text=text,
                kind="plain_text",
                source="pubmed",
            )
        append_error(errors, "PubMed efetch returned empty abstract")
        return None
    except requests.RequestException as exc:
        append_error(errors, f"PubMed efetch request failed: {exc.__class__.__name__}")
        return None
