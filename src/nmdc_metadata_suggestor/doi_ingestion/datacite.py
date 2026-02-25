"""DataCite DOI resolver."""

import requests

from nmdc_metadata_suggestor.constants import (
    DATACITE_API_URL,
    DEFAULT_TIMEOUT,
    USER_AGENT,
)
from nmdc_metadata_suggestor.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    request_with_retry,
)
from nmdc_metadata_suggestor.doi_ingestion.resolver_context import ResolverContext


def try_datacite(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return cleaned/raw text from DataCite descriptions, preferring abstract."""
    try:
        response = request_with_retry(
            "GET",
            f"{DATACITE_API_URL}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"DataCite API returned HTTP {response.status_code}")
            return None
        attrs = response.json().get("data", {}).get("attributes", {})
        descriptions = attrs.get("descriptions")
        publisher = attrs.get("publisher")
        if not isinstance(descriptions, list):
            append_error(errors, "DataCite response missing descriptions")
            return None
    except requests.RequestException as exc:
        append_error(errors, f"DataCite API request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(errors, "DataCite API returned invalid JSON")
        return None

    preferred = _pick_datacite_description(descriptions)
    if preferred is None:
        append_error(errors, "DataCite contained no usable description entries")
        return None

    raw, kind = preferred
    cleaned = clean_text(raw)
    if not cleaned:
        append_error(errors, "DataCite context was empty after cleaning")
        return None

    publisher_value = publisher if isinstance(publisher, str) else None
    return ResolverContext(text=cleaned, raw_text=raw, kind=kind, source=publisher_value)


def _pick_datacite_description(descriptions: list[object]) -> tuple[str, str] | None:
    """Pick preferred DataCite description entry: abstract, then first fallback."""
    abstract_candidate: str | None = None
    description_candidate: str | None = None

    for item in descriptions:
        if not isinstance(item, dict):
            continue
        text = item.get("description")
        if not isinstance(text, str) or not text.strip():
            continue
        description_type = item.get("descriptionType")
        if isinstance(description_type, str) and description_type.lower() == "abstract":
            if abstract_candidate is None:
                abstract_candidate = text
        elif description_candidate is None:
            description_candidate = text

    if abstract_candidate is not None:
        return abstract_candidate, "abstract"
    if description_candidate is not None:
        return description_candidate, "description"
    return None
