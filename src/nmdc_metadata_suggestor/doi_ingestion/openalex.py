import requests
import json
from nmdc_metadata_suggestor.constants import (
    OPENALEX_API_URL,
    DEFAULT_TIMEOUT,
    USER_AGENT,
)

from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    request_with_retry, decode_inverted_abstract
)
from nmdc_metadata_suggestor.doi_ingestion.resolver_context import ResolverContext


def try_openalex(doi: str) -> ResolverContext:
    """Fetch abstract from OpenAlex.

    OpenAlex stores abstracts as an inverted index: ``{word: [positions...]}``.
    We reconstruct plain text by sorting on position.

    Returns:
        ResolverContext instance.
    """
    try:
        response = request_with_retry(
            "GET",
            f"{OPENALEX_API_URL}/https://doi.org/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return ResolverContext(None, None, None)
        data = response.json()
        inverted = data.get("abstract_inverted_index")
        if inverted:
            raw = json.dumps(inverted, ensure_ascii=False)
            return ResolverContext(decode_inverted_abstract(inverted), raw, "inverted_index")
        return ResolverContext(None, None, None)
    except (requests.RequestException, ValueError):
        return ResolverContext(None, None, None)