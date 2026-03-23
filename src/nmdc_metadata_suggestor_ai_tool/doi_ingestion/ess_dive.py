"""ESS-DIVE DOI resolver."""

import re
import xml.etree.ElementTree as ET

import requests

from nmdc_metadata_suggestor_ai_tool.constants import (
    DATAONE_CN_OBJECT_API,
    DATAONE_CN_SOLR_API,
    DEFAULT_TIMEOUT,
    ESS_DIVE_API,
    MAX_DATAONE_SOLR_XML_CHARS,
    UNSAFE_XML_DECLARATION_PATTERN,
    USER_AGENT,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    append_error,
    clean_text,
    extract_document_urls_from_file_entries,
    extract_doi_references,
    extract_related_publication_dois,
    find_first_text,
    merge_unique_strings,
    normalize_doi,
    request_with_retry,
)
from nmdc_metadata_suggestor_ai_tool.models.resolver_context import ResolverContext


def try_ess_dive(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return context from ESS-DIVE if available."""
    accumulated_urls: list[str] | None = None
    accumulated_dois: list[str] | None = None

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
            publication_urls, publication_dois = _extract_ess_dive_publication_metadata(
                payload,
                requested_doi=doi,
            )
            accumulated_urls = merge_unique_strings(accumulated_urls, publication_urls)
            accumulated_dois = merge_unique_strings(accumulated_dois, publication_dois)
            extracted = find_first_text(payload, keys=("abstract", "description"))
            if extracted is not None:
                key, text = extracted
                cleaned = clean_text(text)
                if cleaned:
                    return ResolverContext(
                        text=cleaned,
                        raw_text=text,
                        kind="abstract" if key == "abstract" else "description",
                        urls=accumulated_urls,
                        publication_dois=accumulated_dois,
                    )
        else:
            append_error(errors, f"ESS-DIVE API returned HTTP {response.status_code}")
    except (requests.RequestException, ValueError):
        append_error(errors, "ESS-DIVE API request failed")

    # ESS-DIVE's Dataset API may require auth for anonymous callers.
    context = _try_ess_dive_dataone(doi, errors=errors)
    if context is not None:
        return ResolverContext(
            text=context.text,
            raw_text=context.raw_text,
            kind=context.kind,
            source=context.source,
            urls=merge_unique_strings(accumulated_urls, context.urls),
            publication_dois=merge_unique_strings(accumulated_dois, context.publication_dois),
        )
    if accumulated_urls or accumulated_dois:
        return ResolverContext(
            text=None,
            raw_text=None,
            kind="description",
            urls=accumulated_urls,
            publication_dois=accumulated_dois,
        )
    append_error(errors, "ESS-DIVE DataONE fallback returned no usable context")
    return None


def _try_ess_dive_dataone(doi: str, errors: list[str] | None = None) -> ResolverContext | None:
    """Return ESS-DIVE context from DataONE's public Solr index by DOI."""
    for query in _build_ess_dive_dataone_queries(doi):
        docs = _fetch_dataone_solr_docs(query, errors=errors)
        if not docs:
            continue
        context = _extract_ess_dive_dataone_context(docs, doi, errors=errors)
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
    """Parse DataONE Solr XML response into a list of document dictionaries.

    For safety when parsing untrusted XML, this rejects oversized payloads and
    any payload containing DTD/entity declarations before calling ElementTree.
    """
    if not isinstance(xml_text, str):
        return []
    if len(xml_text) > MAX_DATAONE_SOLR_XML_CHARS:
        return []
    if UNSAFE_XML_DECLARATION_PATTERN.search(xml_text):
        return []

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
    docs: list[dict[str, object]],
    requested_doi: str,
    errors: list[str] | None = None,
) -> ResolverContext | None:
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

    sorted_docs = _sort_dataone_docs_for_context(exact_matches or fallback_matches)

    for doc in sorted_docs:
        selected = _extract_ess_dive_dataone_doc_text(doc)
        if selected is None:
            continue
        raw, kind = selected
        publication_dois = _extract_ess_dive_dataone_publication_dois(
            doc,
            requested_doi=requested_doi,
            errors=errors,
        )
        return ResolverContext(
            text=clean_text(raw),
            raw_text=raw,
            kind=kind,
            publication_dois=publication_dois,
        )

    for doc in sorted_docs:
        publication_dois = _extract_ess_dive_dataone_publication_dois(
            doc,
            requested_doi=requested_doi,
            errors=errors,
        )
        if publication_dois:
            return ResolverContext(
                text=None,
                raw_text=None,
                kind="description",
                publication_dois=publication_dois,
            )
    return None


def _extract_ess_dive_dataone_doc_text(
    doc: dict[str, object],
) -> tuple[str, str] | None:
    """Return the preferred cleaned text-bearing field from a DataONE doc."""
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
            return raw, kind
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


def _extract_ess_dive_publication_metadata(
    payload: object, requested_doi: str
) -> tuple[list[str] | None, list[str] | None]:
    """Extract direct document links and related publication DOIs from ESS-DIVE payloads."""
    publication_urls: list[str] | None = None
    publication_dois: list[str] | None = None

    data_entries = payload.get("data") if isinstance(payload, dict) else None
    records = data_entries if isinstance(data_entries, list) else [payload]

    for record in records:
        if not isinstance(record, dict):
            continue

        for key in ("files", "documents", "publications"):
            publication_urls = merge_unique_strings(
                publication_urls,
                extract_document_urls_from_file_entries(record.get(key)),
            )

        for key in ("relatedIdentifiers", "related_identifiers", "related_materials"):
            publication_dois = merge_unique_strings(
                publication_dois,
                extract_related_publication_dois(record.get(key), requested_doi=requested_doi),
            )

        for key in ("publicationDOI", "publication_doi", "resource_doi"):
            value = record.get(key)
            if isinstance(value, str):
                publication_dois = merge_unique_strings(
                    publication_dois,
                    extract_doi_references(value, requested_doi=requested_doi),
                )

    return publication_urls, publication_dois


def _extract_ess_dive_dataone_publication_dois(
    doc: dict[str, object],
    requested_doi: str,
    errors: list[str] | None = None,
) -> list[str] | None:
    """Extract linked publication DOIs from a public DataONE EML object."""
    identifier = _extract_dataone_doc_string(doc.get("id"))
    if identifier is None:
        return None

    xml_text = _fetch_dataone_object_xml(identifier, errors=errors)
    if xml_text is None:
        return None

    if len(xml_text) > MAX_DATAONE_SOLR_XML_CHARS:
        append_error(errors, "DataONE object XML exceeded size limit")
        return None
    if UNSAFE_XML_DECLARATION_PATTERN.search(xml_text):
        append_error(errors, "DataONE object XML contains unsafe declarations")
        return None

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        append_error(errors, "DataONE object XML was not valid XML")
        return None

    publication_dois: list[str] | None = None
    for section in root.iter():
        if _xml_local_name(section.tag) != "section":
            continue

        section_title = ""
        section_text_parts: list[str] = []
        for child in section:
            local_name = _xml_local_name(child.tag)
            child_text = clean_text("".join(child.itertext()))
            if not child_text:
                continue
            if local_name == "title":
                section_title = child_text.lower()
            elif local_name == "para":
                section_text_parts.append(child_text)

        if "related references" not in section_title:
            continue

        publication_dois = merge_unique_strings(
            publication_dois,
            extract_doi_references(
                " ".join(section_text_parts),
                requested_doi=requested_doi,
            ),
        )

    return publication_dois


def _fetch_dataone_object_xml(
    identifier: str,
    errors: list[str] | None = None,
) -> str | None:
    """Fetch a public DataONE metadata object by identifier."""
    try:
        response = request_with_retry(
            "GET",
            f"{DATAONE_CN_OBJECT_API}/{identifier}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        append_error(
            errors,
            f"DataONE object request failed: {exc.__class__.__name__}",
        )
        return None

    if response.status_code != 200:
        append_error(
            errors,
            f"DataONE object request returned HTTP {response.status_code}",
        )
        return None
    return response.text


def _xml_local_name(tag: str) -> str:
    """Return XML localname for a namespaced ElementTree tag."""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag
