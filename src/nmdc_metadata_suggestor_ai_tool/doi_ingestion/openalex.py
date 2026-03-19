import json

import requests

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_TIMEOUT,
    OPENALEX_API_URL,
    USER_AGENT,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    append_error,
    decode_inverted_abstract,
    request_with_retry,
)
from nmdc_metadata_suggestor_ai_tool.models.resolver_context import ResolverContext


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
            append_error(errors, f"OpenAlex API returned HTTP {response.status_code}")
            return None
        data = response.json()
        inverted = data.get("abstract_inverted_index")
        if inverted:
            raw = json.dumps(inverted, ensure_ascii=False)
            return ResolverContext(decode_inverted_abstract(inverted), raw, "inverted_index")
        append_error(errors, "OpenAlex response contained no abstract_inverted_index")
        return None
    except (requests.RequestException, ValueError) as exc:
        append_error(errors, f"OpenAlex API request failed: {exc.__class__.__name__}")
        return None
