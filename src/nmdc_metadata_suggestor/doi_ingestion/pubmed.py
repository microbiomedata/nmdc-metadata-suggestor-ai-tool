import requests

from nmdc_metadata_suggestor.constants import (
    DEFAULT_TIMEOUT,
    PUBMED_EFETCH,
    PUBMED_ID_CONVERTER,
    USER_AGENT,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    request_with_retry,
)
from nmdc_metadata_suggestor.models.doi import SourceRetrievalResult


def try_pubmed(doi: str, errors: list[str] | None = None) -> SourceRetrievalResult | None:
    """Fetch abstract from PubMed via DOI -> PMID -> efetch.

    Returns:
        SourceRetrievalResult with the abstract text and PMID, or None if PubMed has no record.
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
            return SourceRetrievalResult(
                doi=doi,
                context=text,
                raw_context=text,
                context_type="plain_text",
                source="pubmed",
                pmid=pmid,
            )
        append_error(errors, "PubMed efetch returned empty abstract")
        return None
    except requests.RequestException as exc:
        append_error(errors, f"PubMed efetch request failed: {exc.__class__.__name__}")
        return None
