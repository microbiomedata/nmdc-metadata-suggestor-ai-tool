"""Archived provider-specific publication parsing helpers.

These helpers were removed from the active DOI ingestion path in favor of the
shared generic publication lookup flow, but are kept here for reference in case
provider-specific publication parsing becomes useful again.
"""

import re
from urllib.parse import parse_qs, urlparse

import requests

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_TIMEOUT,
    PROXI_DATASETS_API,
    USER_AGENT,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    extract_document_urls_from_file_entries,
    extract_doi_references,
    extract_related_publication_dois,
    merge_unique_strings,
    request_with_retry,
    text_mentions_doi,
)

MASSIVE_PUBLICATIONS_BLOCK_PATTERN = re.compile(
    r"<h2>\s*Publications\s*</h2>(.*?)</div>",
    re.IGNORECASE | re.DOTALL,
)


def search_massive_accessions_by_doi(doi: str, errors: list[str] | None = None) -> list[str]:
    """Search PROXI dataset rows for publication cells mentioning the DOI."""
    query_values = [doi, f"https://doi.org/{doi}", f"doi:{doi}"]
    seen: set[str] = set()
    accessions: list[str] = []

    for publication_query in query_values:
        rows = fetch_massive_proxi_dataset_rows(publication_query, errors=errors)
        for row in rows:
            accession = extract_massive_proxi_row_accession_for_doi(row, doi)
            if accession is None or accession in seen:
                continue
            seen.add(accession)
            accessions.append(accession)

    return accessions


def fetch_massive_proxi_dataset_rows(
    publication_query: str, errors: list[str] | None = None
) -> list[list[object]]:
    """Return PROXI dataset table rows for a publication-filtered query."""
    try:
        response = request_with_retry(
            "GET",
            PROXI_DATASETS_API,
            params={
                "publication": publication_query,
                "repository": "MassIVE",
                "resultType": "full",
                "pageSize": 100,
                "pageNumber": 1,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"PROXI dataset rows request returned HTTP {response.status_code}")
            return []
        payload = response.json()
    except requests.RequestException as exc:
        append_error(errors, f"PROXI dataset rows request failed: {exc.__class__.__name__}")
        return []
    except ValueError:
        append_error(errors, "PROXI dataset rows request returned invalid JSON")
        return []

    datasets = payload.get("datasets")
    if not isinstance(datasets, list):
        return []

    rows: list[list[object]] = []
    for row in datasets:
        if isinstance(row, list):
            rows.append(row)
    return rows


def extract_massive_proxi_row_accession_for_doi(row: list[object], doi: str) -> str | None:
    """Return dataset accession if a PROXI row references the DOI."""
    if not row:
        return None

    accession = row[0] if len(row) > 0 else None
    publication_cell = row[7] if len(row) > 7 else None
    if not isinstance(accession, str):
        return None
    if not isinstance(publication_cell, str):
        return None
    if not text_mentions_doi(publication_cell, doi):
        return None

    cleaned_accession = accession.strip().upper()
    if not cleaned_accession:
        return None
    return cleaned_accession


def collect_massive_landing_page_candidates(
    redirect_location: str | None, accessions: list[str]
) -> list[str]:
    """Return likely MassIVE dataset landing pages for HTML fallback scraping."""
    candidates: list[str] = []
    seen: set[str] = set()

    if isinstance(redirect_location, str) and redirect_location.strip():
        parsed = urlparse(redirect_location.strip())
        if parsed.netloc.lower() == "massive.ucsd.edu":
            normalized = redirect_location.strip()
            seen.add(normalized)
            candidates.append(normalized)

            accession_values = parse_qs(parsed.query).get("accession", [])
            for value in accession_values:
                accession = value.strip().upper()
                if accession:
                    seen.add(accession)

    for accession in accessions:
        page_url = f"https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={accession}"
        if page_url in seen:
            continue
        seen.add(page_url)
        candidates.append(page_url)

    return candidates


def extract_massive_landing_page_publication_dois(
    html_text: str, requested_doi: str
) -> list[str] | None:
    """Extract publication DOIs from the MassIVE landing page publications block."""
    publication_dois: list[str] | None = None

    for match in MASSIVE_PUBLICATIONS_BLOCK_PATTERN.finditer(html_text):
        publication_text = clean_text(match.group(1))
        publication_dois = merge_unique_strings(
            publication_dois,
            extract_doi_references(publication_text, requested_doi=requested_doi),
        )

    return publication_dois


def extract_massive_publication_metadata(
    payload: dict[str, object], requested_doi: str
) -> tuple[list[str] | None, list[str] | None]:
    """Extract publication file URLs and linked publication DOIs from PROXI payloads."""
    publication_urls: list[str] | None = None
    publication_dois: list[str] | None = None

    for key in ("files", "datasetFiles", "publications", "fullDatasetLinks", "links"):
        value = payload.get(key)
        publication_urls = merge_unique_strings(
            publication_urls,
            extract_document_urls_from_file_entries(value),
        )
        publication_dois = merge_unique_strings(
            publication_dois,
            extract_related_publication_dois(value, requested_doi=requested_doi),
        )
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    publication_dois = merge_unique_strings(
                        publication_dois,
                        extract_doi_references(item, requested_doi=requested_doi),
                    )

    for key in ("publication", "citation", "publication_doi"):
        value = payload.get(key)
        if isinstance(value, str):
            publication_dois = merge_unique_strings(
                publication_dois,
                extract_doi_references(value, requested_doi=requested_doi),
            )

    return publication_urls, publication_dois
