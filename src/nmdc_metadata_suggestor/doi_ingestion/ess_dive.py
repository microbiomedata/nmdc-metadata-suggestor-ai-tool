"""ESS-DIVE DOI resolver."""

import re
import xml.etree.ElementTree as ET

import requests

from nmdc_metadata_suggestor.doi_ingestion.common import append_error, clean_text, find_first_text
from nmdc_metadata_suggestor.publication_ingestion.doi_utils import (
    DEFAULT_TIMEOUT,
    USER_AGENT,
    normalize_doi,
    request_with_retry,
)

ESS_DIVE_API = "https://api.ess-dive.lbl.gov/packages"
DATAONE_CN_SOLR_API = "https://cn.dataone.org/cn/v2/query/solr/"


def try_ess_dive(doi: str, errors: list[str] | None = None) -> tuple[str, str] | None:
    """Return ``(text, kind)`` from ESS-DIVE if available."""
    try:
        response = request_with_retry(
            "GET",
            ESS_DIVE_API,
            params={"doi": doi},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code == 200:
            payload = response.json()
            extracted = find_first_text(payload, keys=("abstract", "description"))
            if extracted is not None:
                key, text = extracted
                cleaned = clean_text(text)
                if cleaned:
                    return cleaned, "abstract" if key == "abstract" else "description"
        else:
            append_error(errors, f"ESS-DIVE API returned HTTP {response.status_code}")
    except (requests.RequestException, ValueError):
        append_error(errors, "ESS-DIVE API request failed")

    # ESS-DIVE's Dataset API may require auth for anonymous callers.
    context = _try_ess_dive_dataone(doi, errors=errors)
    if context is None:
        append_error(errors, "ESS-DIVE DataONE fallback returned no usable context")
    return context


def _try_ess_dive_dataone(
    doi: str, errors: list[str] | None = None
) -> tuple[str, str] | None:
    """Return ESS-DIVE context from DataONE's public Solr index by DOI."""
    for query in _build_ess_dive_dataone_queries(doi):
        docs = _fetch_dataone_solr_docs(query, errors=errors)
        if not docs:
            continue
        context = _extract_ess_dive_dataone_context(docs, doi)
        if context is not None:
            return context
    return None


def _build_ess_dive_dataone_queries(doi: str) -> list[str]:
    """Build DataONE Solr query candidates for an ESS-DIVE DOI."""
    candidates: list[str] = [doi, doi.upper()]
    suffix = doi.rsplit("/", 1)[-1]
    if suffix and suffix not in candidates:
        candidates.append(suffix)

    queries: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        queries.append(f'datasource:"urn:node:ESS_DIVE" AND seriesId:*{candidate}*')
    return queries


def _fetch_dataone_solr_docs(
    query: str, errors: list[str] | None = None
) -> list[dict[str, object]]:
    """Fetch DataONE Solr docs for a query string."""
    try:
        response = request_with_retry(
            "GET",
            DATAONE_CN_SOLR_API,
            params={
                "q": query,
                "rows": 25,
                "fl": (
                    "id,seriesId,title,abstract,description,summary,datasource,"
                    "dateUploaded,datePublished"
                ),
            },
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"DataONE Solr returned HTTP {response.status_code}")
            return []
        return _parse_dataone_solr_docs(response.text)
    except requests.RequestException as exc:
        append_error(errors, f"DataONE Solr request failed: {exc.__class__.__name__}")
        return []


def _parse_dataone_solr_docs(xml_text: str) -> list[dict[str, object]]:
    """Parse DataONE Solr XML response into a list of document dictionaries."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    docs: list[dict[str, object]] = []
    for doc in root.findall(".//doc"):
        parsed: dict[str, object] = {}
        for field in doc:
            name = field.attrib.get("name")
            if not isinstance(name, str) or not name:
                continue
            if field.tag == "arr":
                values = [
                    item.text.strip()
                    for item in field
                    if isinstance(item.text, str) and item.text.strip()
                ]
                if values:
                    parsed[name] = values
                continue

            if isinstance(field.text, str) and field.text.strip():
                parsed[name] = field.text.strip()
        if parsed:
            docs.append(parsed)
    return docs


def _extract_ess_dive_dataone_context(
    docs: list[dict[str, object]], requested_doi: str
) -> tuple[str, str] | None:
    """Extract abstract/description context from DataONE ESS-DIVE docs."""
    exact_matches: list[dict[str, object]] = []
    fallback_matches: list[dict[str, object]] = []

    for doc in docs:
        datasource = _extract_dataone_doc_string(doc.get("datasource"))
        if datasource and datasource != "urn:node:ESS_DIVE":
            continue

        series_id = _extract_dataone_doc_string(doc.get("seriesId"))
        if _ess_dive_series_id_matches_doi(series_id, requested_doi):
            exact_matches.append(doc)
        else:
            fallback_matches.append(doc)

    for doc in _sort_dataone_docs_for_context(exact_matches or fallback_matches):
        for key, kind in (
            ("abstract", "abstract"),
            ("description", "description"),
            ("summary", "description"),
            ("title", "description"),
        ):
            raw = _extract_dataone_doc_string(doc.get(key))
            if not raw:
                continue
            cleaned = clean_text(raw)
            if cleaned:
                return cleaned, kind
    return None


def _extract_dataone_doc_string(value: object) -> str | None:
    """Return the first non-empty string from a DataONE document field."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _ess_dive_series_id_matches_doi(series_id: str | None, requested_doi: str) -> bool:
    """Return True when DataONE ``seriesId`` corresponds to the requested DOI."""
    if not isinstance(series_id, str) or not series_id.strip():
        return False
    stripped = re.sub(r"^doi:", "", series_id, count=1, flags=re.IGNORECASE)
    normalized_series = normalize_doi(stripped).lower()
    return normalized_series == requested_doi.lower()


def _sort_dataone_docs_for_context(docs: list[dict[str, object]]) -> list[dict[str, object]]:
    """Sort DataONE docs by newest upload/publication timestamp first."""
    return sorted(
        docs,
        key=lambda doc: (
            _extract_dataone_doc_string(doc.get("dateUploaded")) or "",
            _extract_dataone_doc_string(doc.get("datePublished")) or "",
        ),
        reverse=True,
    )
