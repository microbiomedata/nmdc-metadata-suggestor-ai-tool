"""DataCite DOI resolver."""

import requests

from nmdc_metadata_suggestor_ai_tool.constants import (
    DATACITE_API_URL,
    DEFAULT_TIMEOUT,
    USER_AGENT,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    extract_related_publication_dois,
    merge_unique_strings,
    normalize_http_url,
    request_with_retry,
)
from nmdc_metadata_suggestor_ai_tool.models.resolver_context import ResolverContext
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.retreive_pdf_link import (
    retrieve_pdf_link_from_crossref,
    retrieve_pdf_link_from_pmc,
)

type DataciteAttributes = dict[str, object]
type DataciteAttributesCache = dict[str, DataciteAttributes]


def try_datacite(
    doi: str,
    errors: list[str] | None = None,
    cache: DataciteAttributesCache | None = None,
) -> ResolverContext | None:
    """Return cleaned/raw text from DataCite descriptions, preferring abstract."""
    attrs = fetch_datacite_attributes(doi, errors=errors, cache=cache)
    if attrs is None:
        return None

    descriptions = attrs.get("descriptions")
    publisher = attrs.get("publisher")
    if not isinstance(descriptions, list):
        append_error(errors, "DataCite response missing descriptions")
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


def try_datacite_related_publication_lookup(
    doi: str,
    errors: list[str] | None = None,
    cache: DataciteAttributesCache | None = None,
) -> ResolverContext | None:
    """Return DataCite-derived related publication DOI/PDF metadata."""
    attrs = fetch_datacite_attributes(doi, errors=errors, cache=cache)
    if attrs is None:
        return None

    publication_dois = extract_related_publication_dois_from_datacite_attributes(
        attrs,
        requested_doi=doi,
    )
    if not publication_dois:
        return None

    publication_urls: list[str] | None = None
    for publication_doi in publication_dois:
        publication_urls = merge_unique_strings(
            publication_urls,
            _lookup_publication_urls(publication_doi),
        )

    return ResolverContext(
        text=None,
        raw_text=None,
        kind="publication_lookup",
        source="datacite",
        urls=publication_urls,
        publication_dois=publication_dois,
    )


def fetch_datacite_attributes(
    doi: str,
    errors: list[str] | None = None,
    cache: DataciteAttributesCache | None = None,
) -> DataciteAttributes | None:
    """Fetch DataCite attributes for a DOI, using request-scoped cache when available."""
    if cache is not None and doi in cache:
        return cache[doi]

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
    except requests.RequestException as exc:
        append_error(errors, f"DataCite API request failed: {exc.__class__.__name__}")
        return None
    except ValueError:
        append_error(errors, "DataCite API returned invalid JSON")
        return None

    if not isinstance(attrs, dict):
        append_error(errors, "DataCite response missing attributes")
        return None

    if cache is not None:
        cache[doi] = attrs
    return attrs


def extract_related_publication_dois_from_datacite_attributes(
    attrs: DataciteAttributes,
    requested_doi: str,
) -> list[str] | None:
    """Extract related publication DOIs from a DataCite attributes payload."""
    related_identifiers = attrs.get("relatedIdentifiers")
    return (
        extract_related_publication_dois(
            related_identifiers,
            requested_doi=requested_doi,
        )
        or None
    )


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


def _lookup_publication_urls(publication_doi: str) -> list[str] | None:
    """Resolve likely PDF URLs for a publication DOI through generic resolvers."""
    publication_urls: list[str] | None = None

    crossref_result = retrieve_pdf_link_from_crossref(publication_doi)
    for candidate in crossref_result.get("pdf_links", []):
        normalized = normalize_http_url(candidate)
        if normalized is None:
            continue
        publication_urls = merge_unique_strings(publication_urls, [normalized])

    pmc_result = retrieve_pdf_link_from_pmc(publication_doi)
    pmc_pdf_url = normalize_http_url(pmc_result.get("pdf_url"))
    if pmc_pdf_url is not None:
        publication_urls = merge_unique_strings(publication_urls, [pmc_pdf_url])

    return publication_urls
