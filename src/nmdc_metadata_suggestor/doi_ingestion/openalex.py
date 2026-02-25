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
from nmdc_metadata_suggestor.models.resolver_context import ResolverContext


def try_openalex(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Fetch abstract from OpenAlex.

    OpenAlex stores abstracts as an inverted index: ``{word: [positions...]}``.
    We reconstruct plain text by sorting on position.

    Returns:
        ResolverContext instance, or None if no abstract was found.
    """
    try:
        response = request_with_retry(
            "GET",
            f"{OPENALEX_API_URL}/https://doi.org/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            if errors is not None:
                errors.append(f"OpenAlex API returned HTTP {response.status_code}")
            return None
        data = response.json()
        inverted = data.get("abstract_inverted_index")
        if inverted:
            raw = json.dumps(inverted, ensure_ascii=False)
            return ResolverContext(decode_inverted_abstract(inverted), raw, "inverted_index")
        if errors is not None:
            errors.append("OpenAlex response contained no abstract_inverted_index")
        return None
    except (requests.RequestException, ValueError) as exc:
        if errors is not None:
            errors.append(f"OpenAlex API request failed: {exc.__class__.__name__}")
        return None