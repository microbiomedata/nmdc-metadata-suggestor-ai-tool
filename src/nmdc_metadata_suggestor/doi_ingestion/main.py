"""DOI description/abstract ingestion for repository-backed DOI providers.

This module retrieves text context for LLM prompting from DOI records, with a
waterfall that prioritizes provider APIs and then falls back to generic DOI
metadata APIs.
"""

import html
import re
import xml.etree.ElementTree as ET

import requests

from nmdc_metadata_suggestor.models.doi import DoiContextResult
from nmdc_metadata_suggestor.publication_ingestion.abstract_retriever import strip_jats_xml
from nmdc_metadata_suggestor.publication_ingestion.doi_utils import (
    CROSSREF_API,
    DATACITE_API,
    DEFAULT_TIMEOUT,
    DOI_PATTERN,
    USER_AGENT,
    normalize_doi,
)

DOI_CONTENT_NEGOTIATION_API = "https://doi.org"
EDI_DOI_API = "https://pasta.lternet.edu/package/doi"
EMSL_PROJECTS_API = "https://api.emsl.pnnl.gov/external/projects"
ESS_DIVE_API = "https://api.ess-dive.lbl.gov/packages"
FIGSHARE_API = "https://api.figshare.com/v2/articles"
FIGSHARE_COLLECTIONS_API = "https://api.figshare.com/v2/collections"
JGI_SEARCH_API = "https://files.jgi.doe.gov/search/"
KBASE_SEARCH_API = "https://kbase.us/services/searchapi2/rpc"
KBASE_WORKSPACE_API = "https://kbase.us/services/ws"
CYVERSE_METADATA_API = "https://de.cyverse.org/terrain/filesystem/metadata"
CYVERSE_METADATA_SEARCH_API = f"{CYVERSE_METADATA_API}/search"
PROXI_DATASETS_API = "https://proteomecentral.proteomexchange.org/api/proxi/v0.1/datasets"
ZENODO_API = "https://zenodo.org/api/records"

TARGET_PROVIDER_PREFIXES: dict[str, str] = {
    "10.6073": "edi",
    "10.46936": "emsl",
    "10.15485": "ess-dive",
    "10.21952": "ess-dive",
    "10.6084": "figshare",
    "10.25585": "jgi",
    "10.25982": "kbase",
    "10.25345": "massive",
    "10.17504": "cyverse",
    "10.5281": "zenodo",
}

TARGET_PROVIDER_KEYWORDS: dict[str, str] = {
    "environmental data initiative": "edi",
    "emsl": "emsl",
    "ess-dive": "ess-dive",
    "figshare": "figshare",
    "genomic standards consortium": "gsc",
    "jgi": "jgi",
    "kbase": "kbase",
    "massive": "massive",
    "zenodo": "zenodo",
    "cyverse": "cyverse",
}

ALL_SOURCES = (
    "edi",
    "emsl",
    "ess-dive",
    "figshare",
    "jgi",
    "kbase",
    "massive",
    "cyverse",
    "zenodo",
    "datacite",
    "crossref",
    "content_negotiation",
)


def get_doi_description_or_abstract(
    doi: str, sources: list[str] | None = None
) -> DoiContextResult:
    """Fetch abstract/description text for a DOI using a source waterfall.

    Args:
        doi: DOI in any common format.
        sources: Optional explicit source order. Supported values are in
            ``ALL_SOURCES``.

    Returns:
        DoiContextResult with the first abstract/description found.
    """
    doi = normalize_doi(doi)
    if not DOI_PATTERN.match(doi):
        return DoiContextResult(doi=doi, error="Invalid DOI: Malformed DOI syntax")

    provider = _infer_provider_from_doi(doi)
    if sources is None:
        sources = _default_source_order(provider)

    attempts: list[str] = []
    for source in sources:
        fetcher = _SOURCE_FETCHERS.get(source)
        if fetcher is None:
            continue
        attempts.append(source)
        result = fetcher(doi, provider, attempts)
        if result is not None:
            return result

    return DoiContextResult(
        doi=doi,
        provider=provider,
        attempts=attempts,
        error="No description or abstract found in any source",
    )


def _default_source_order(provider: str | None) -> list[str]:
    """Return the default source order for a provider-aware lookup."""
    if provider in _PROVIDER_API_SOURCES:
        return [provider, "datacite", "crossref", "content_negotiation"]
    return ["datacite", "crossref", "content_negotiation"]


def _infer_provider_from_doi(doi: str) -> str | None:
    """Infer a provider key from DOI prefix mapping."""
    prefix_match = re.match(r"^(10\.\d{4,9})/", doi)
    if not prefix_match:
        return None
    prefix = prefix_match.group(1)
    return TARGET_PROVIDER_PREFIXES.get(prefix)


def _infer_provider_from_text(text: str | None) -> str | None:
    """Infer a provider key from publisher/source text keywords."""
    if not text:
        return None
    lowered = text.lower()
    for key, provider in TARGET_PROVIDER_KEYWORDS.items():
        if key in lowered:
            return provider
    return None


def _build_result(
    doi: str,
    provider: str | None,
    source: str,
    attempts: list[str],
    context: str,
    raw_context: str,
    context_type: str,
) -> DoiContextResult:
    """Construct a normalized context result object."""
    return DoiContextResult(
        doi=doi,
        context=context,
        raw_context=raw_context,
        context_type=context_type,
        provider=provider,
        source=source,
        attempts=attempts,
    )


def _fetch_ess_dive(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context from ESS-DIVE-specific API."""
    context = _try_ess_dive(doi)
    if context is None:
        return None
    text, kind = context
    return _build_result(doi, provider, "ess-dive", attempts, text, text, kind)


def _fetch_edi(doi: str, provider: str | None, attempts: list[str]) -> DoiContextResult | None:
    """Fetch and wrap context from EDI's PASTA API."""
    context = _try_edi(doi)
    if context is None:
        return None
    text, kind = context
    return _build_result(doi, provider, "edi", attempts, text, text, kind)


def _fetch_emsl(doi: str, provider: str | None, attempts: list[str]) -> DoiContextResult | None:
    """Fetch and wrap context from EMSL project API."""
    context = _try_emsl(doi)
    if context is None:
        return None
    text, raw, kind = context
    return _build_result(doi, provider, "emsl", attempts, text, raw, kind)


def _fetch_figshare(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context from Figshare-specific API."""
    text = _try_figshare(doi)
    if text is None:
        return None
    return _build_result(doi, provider, "figshare", attempts, text, text, "description")


def _fetch_jgi(doi: str, provider: str | None, attempts: list[str]) -> DoiContextResult | None:
    """Fetch and wrap context from JGI search API."""
    context = _try_jgi(doi)
    if context is None:
        return None
    text, raw, kind = context
    return _build_result(doi, provider, "jgi", attempts, text, raw, kind)


def _fetch_kbase(doi: str, provider: str | None, attempts: list[str]) -> DoiContextResult | None:
    """Fetch and wrap context from KBase Search API."""
    context = _try_kbase(doi)
    if context is None:
        return None
    text, raw, kind = context
    return _build_result(doi, provider, "kbase", attempts, text, raw, kind)


def _fetch_massive(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context from MassIVE via ProteomeCentral PROXI."""
    context = _try_massive(doi)
    if context is None:
        return None
    text, raw, kind = context
    return _build_result(doi, provider, "massive", attempts, text, raw, kind)


def _fetch_cyverse(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context from CyVerse Terrain metadata APIs."""
    context = _try_cyverse(doi)
    if context is None:
        return None
    text, raw, kind = context
    return _build_result(doi, provider, "cyverse", attempts, text, raw, kind)


def _fetch_zenodo(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context from Zenodo-specific API."""
    text = _try_zenodo(doi)
    if text is None:
        return None
    return _build_result(doi, provider, "zenodo", attempts, text, text, "description")


def _fetch_datacite(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context from DataCite metadata."""
    context = _try_datacite(doi)
    if context is None:
        return None

    text, raw, kind, publisher = context
    inferred_provider = provider or _infer_provider_from_text(publisher)
    return _build_result(doi, inferred_provider, "datacite", attempts, text, raw, kind)


def _fetch_crossref(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context from Crossref metadata."""
    context = _try_crossref(doi)
    if context is None:
        return None

    text, raw, kind, publisher = context
    inferred_provider = provider or _infer_provider_from_text(publisher)
    return _build_result(doi, inferred_provider, "crossref", attempts, text, raw, kind)


def _fetch_content_negotiation(
    doi: str, provider: str | None, attempts: list[str]
) -> DoiContextResult | None:
    """Fetch and wrap context via DOI content negotiation."""
    context = _try_content_negotiation(doi)
    if context is None:
        return None
    text, raw, kind = context
    return _build_result(doi, provider, "content_negotiation", attempts, text, raw, kind)


_SOURCE_FETCHERS = {
    "edi": _fetch_edi,
    "emsl": _fetch_emsl,
    "ess-dive": _fetch_ess_dive,
    "figshare": _fetch_figshare,
    "jgi": _fetch_jgi,
    "kbase": _fetch_kbase,
    "massive": _fetch_massive,
    "cyverse": _fetch_cyverse,
    "zenodo": _fetch_zenodo,
    "datacite": _fetch_datacite,
    "crossref": _fetch_crossref,
    "content_negotiation": _fetch_content_negotiation,
}

_PROVIDER_API_SOURCES = {
    "edi",
    "emsl",
    "ess-dive",
    "figshare",
    "jgi",
    "kbase",
    "massive",
    "cyverse",
    "zenodo",
}


def _try_edi(doi: str) -> tuple[str, str] | None:
    """Return (text, kind) from EDI PASTA metadata resolved by DOI."""
    metadata_url = _resolve_edi_metadata_url(doi)
    if metadata_url is None:
        return None

    try:
        response = requests.get(
            metadata_url,
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        root = ET.fromstring(response.text)
    except (requests.RequestException, ET.ParseError):
        return None

    abstract = _extract_first_xml_text(root, {"abstract"})
    if abstract:
        return abstract, "abstract"

    description = _extract_first_xml_text(root, {"purpose", "description", "title"})
    if description:
        return description, "description"
    return None


def _try_emsl(doi: str) -> tuple[str, str, str] | None:
    """Return cleaned/raw context from EMSL project details resolved by DOI."""
    project_id = _extract_emsl_project_id(doi)
    if project_id is None:
        return None

    try:
        response = requests.get(
            f"{EMSL_PROJECTS_API}/{project_id}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    award_doi = payload.get("award_doi")
    if isinstance(award_doi, str) and award_doi:
        if normalize_doi(award_doi) != doi:
            return None

    abstract = payload.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        cleaned = _clean_text(abstract)
        if cleaned:
            return cleaned, abstract, "abstract"

    for key in ("title", "project_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = _clean_text(value)
            if cleaned:
                return cleaned, value, "description"
    return None


def _try_jgi(doi: str) -> tuple[str, str, str] | None:
    """Return cleaned/raw context from JGI search response."""
    query_candidates: list[tuple[str, bool]] = []

    project_query = _extract_jgi_project_query(doi)
    if project_query:
        query_candidates.append((project_query, True))
    query_candidates.append((doi, False))

    seen_queries: set[str] = set()
    for query_value, use_project_field in query_candidates:
        if query_value in seen_queries:
            continue
        seen_queries.add(query_value)

        params: dict[str, str] = {"q": query_value, "api_version": "2", "x": "10", "p": "1"}
        if use_project_field:
            params["f"] = "project_id"

        try:
            response = requests.get(
                JGI_SEARCH_API,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=DEFAULT_TIMEOUT,
            )
            if response.status_code != 200:
                continue
            payload = response.json()
        except (requests.RequestException, ValueError):
            continue

        context = _extract_jgi_context(payload, doi)
        if context is not None:
            return context

    return None


def _try_kbase(doi: str) -> tuple[str, str, str] | None:
    """Return cleaned/raw context from KBase narrative search by DOI."""
    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "search_objects",
        "params": {
            "query": {"query_string": {"query": f'"{doi}"'}},
            "indexes": ["narrative"],
            "source": ["narrative_title", "cells.desc", "creation_date"],
            "only_public": True,
            "size": 10,
            "from": 0,
            "sort": [{"creation_date": {"order": "desc"}}],
            "track_total_hits": False,
        },
    }

    try:
        response = requests.post(
            KBASE_SEARCH_API,
            json=payload,
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        data = response.json()
    except (requests.RequestException, ValueError):
        return None

    result = data.get("result")
    if not isinstance(result, dict):
        return None

    context = _extract_kbase_context(result, doi)
    if context is None:
        context = _try_kbase_workspace_ref(doi)
        if context is None:
            return None
    return context


def _try_massive(doi: str) -> tuple[str, str, str] | None:
    """Return cleaned/raw context from MassIVE via ProteomeCentral dataset lookup."""
    for accession in _collect_massive_accession_candidates(doi):
        payload = _fetch_proxi_dataset(accession)
        if payload is None:
            continue
        context = _extract_massive_context(payload)
        if context is not None:
            return context

    for accession in _search_proxi_accessions_by_doi(doi):
        payload = _fetch_proxi_dataset(accession)
        if payload is None:
            continue
        context = _extract_massive_context(payload)
        if context is not None:
            return context

    datacite_context = _extract_massive_context_from_datacite_titles(doi)
    if datacite_context is not None:
        return datacite_context
    return None


def _try_cyverse(doi: str) -> tuple[str, str, str] | None:
    """Return cleaned/raw context from CyVerse Terrain metadata."""
    metadata_avus = _search_cyverse_metadata_for_doi(doi)
    if not metadata_avus:
        return None

    context = _extract_cyverse_context(metadata_avus, doi)
    if context is not None:
        return context

    for target_id in _extract_cyverse_target_ids(metadata_avus):
        target_avus = _fetch_cyverse_target_metadata(target_id)
        if not target_avus:
            continue
        context = _extract_cyverse_context(target_avus, doi)
        if context is not None:
            return context
    return None


def _try_ess_dive(doi: str) -> tuple[str, str] | None:
    """Return (text, kind) from ESS-DIVE if available."""
    try:
        response = requests.get(
            ESS_DIVE_API,
            params={"doi": doi},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    extracted = _find_first_text(payload, keys=("abstract", "description"))
    if extracted is None:
        return None
    key, text = extracted
    cleaned = _clean_text(text)
    if not cleaned:
        return None
    return cleaned, "abstract" if key == "abstract" else "description"


def _resolve_edi_metadata_url(doi: str) -> str | None:
    """Resolve EDI metadata URL from an EDI DOI."""
    try:
        response = requests.get(
            f"{EDI_DOI_API}/doi:{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
    except requests.RequestException:
        return None

    for line in response.text.splitlines():
        candidate = line.strip()
        if "/package/metadata/eml/" in candidate:
            return candidate
    return None


def _extract_emsl_project_id(doi: str) -> str | None:
    """Extract EMSL project ID embedded in award DOI suffix."""
    # Example: 10.46936/jejc.proj.2014.48483/60005501 -> 48483
    match = re.search(r"\.proj\.\d{4}\.(\d+)(?:/|$)", doi)
    if match is None:
        return None
    return match.group(1)


def _extract_jgi_project_query(doi: str) -> str | None:
    """Extract likely JGI project identifier from DOI suffix."""
    match = re.match(r"^10\.25585/([^/\s]+)", doi)
    if match is None:
        return None
    token = match.group(1).strip()
    if not token:
        return None
    # JGI dataset DOIs commonly use a numeric identifier here.
    numeric = re.search(r"\d{4,}", token)
    if numeric is not None:
        return numeric.group(0)
    return token


def _extract_jgi_context(payload: object, requested_doi: str) -> tuple[str, str, str] | None:
    """Extract preferred context text from JGI search payload."""
    if not isinstance(payload, dict):
        return None

    proposals = payload.get("proposals")
    if isinstance(proposals, list):
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            if not _jgi_entry_matches_doi(proposal, requested_doi):
                continue

            for key in ("abstract", "lay_description", "description"):
                value = proposal.get(key)
                if isinstance(value, str) and value.strip():
                    cleaned = _clean_text(value)
                    if cleaned:
                        kind = "abstract" if key == "abstract" else "description"
                        return cleaned, value, kind

            for key in ("title", "project_name", "name"):
                value = proposal.get(key)
                if isinstance(value, str) and value.strip():
                    cleaned = _clean_text(value)
                    if cleaned:
                        return cleaned, value, "description"

    organisms = payload.get("organisms")
    if isinstance(organisms, list):
        for organism in organisms:
            if not isinstance(organism, dict):
                continue
            for key in ("title", "name"):
                value = organism.get(key)
                if isinstance(value, str) and value.strip():
                    cleaned = _clean_text(value)
                    if cleaned:
                        return cleaned, value, "description"
    return None


def _jgi_entry_matches_doi(entry: dict[str, object], requested_doi: str) -> bool:
    """Return True when DOI-bearing JGI entry matches the requested DOI."""
    doi_keys = ("doi", "award_doi", "proposal_doi")
    seen_any = False
    for key in doi_keys:
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        seen_any = True
        if normalize_doi(value) == requested_doi:
            return True
    return not seen_any


def _extract_kbase_context(
    result: dict[str, object], requested_doi: str
) -> tuple[str, str, str] | None:
    """Extract description context from KBase narrative search results."""
    hits = result.get("hits")
    if not isinstance(hits, list):
        return None

    best_matching_text: str | None = None
    best_fallback_text: str | None = None
    best_title: str | None = None

    for hit in hits:
        if not isinstance(hit, dict):
            continue
        doc = hit.get("doc")
        if not isinstance(doc, dict):
            continue

        title = doc.get("narrative_title")
        if best_title is None and isinstance(title, str) and title.strip():
            best_title = title

        cells = doc.get("cells")
        if not isinstance(cells, list):
            continue

        for cell in cells:
            if not isinstance(cell, dict):
                continue
            desc = cell.get("desc")
            if not isinstance(desc, str) or not desc.strip():
                continue

            if _text_mentions_doi(desc, requested_doi):
                if best_matching_text is None or len(desc) > len(best_matching_text):
                    best_matching_text = desc
            elif best_fallback_text is None or len(desc) > len(best_fallback_text):
                best_fallback_text = desc

    if best_matching_text:
        cleaned = _clean_text(best_matching_text)
        if cleaned:
            return cleaned, best_matching_text, "description"

    if best_fallback_text:
        cleaned = _clean_text(best_fallback_text)
        if cleaned:
            return cleaned, best_fallback_text, "description"

    if best_title:
        cleaned = _clean_text(best_title)
        if cleaned:
            return cleaned, best_title, "description"
    return None


def _try_kbase_workspace_ref(doi: str) -> tuple[str, str, str] | None:
    """Return context from KBase workspace object info inferred from DOI token."""
    workspace_tokens = _extract_kbase_workspace_tokens(doi)
    if workspace_tokens is None:
        return None
    wsid, token = workspace_tokens

    candidate_refs = [
        f"{wsid}/1/{token}",
        f"{wsid}/{token}",
        f"{wsid}/{token}/1",
    ]

    seen: set[str] = set()
    for ref in candidate_refs:
        if ref in seen:
            continue
        seen.add(ref)
        info = _fetch_kbase_object_info(ref)
        if info is None:
            continue
        context = _extract_kbase_object_info_context(info)
        if context is not None:
            return context
    return None


def _extract_kbase_workspace_tokens(doi: str) -> tuple[str, str] | None:
    """Extract KBase workspace ID and object/version token from DOI."""
    match = re.match(r"^10\.25982/(\d+)\.(\d+)(?:/|$)", doi)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _fetch_kbase_object_info(ref: str) -> list[object] | None:
    """Fetch KBase workspace object info tuple by object reference."""
    payload = {
        "version": "1.1",
        "id": "1",
        "method": "Workspace.get_object_info3",
        "params": [{"objects": [{"ref": ref}], "includeMetadata": 1}],
    }
    try:
        response = requests.post(
            KBASE_WORKSPACE_API,
            json=payload,
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        data = response.json()
    except (requests.RequestException, ValueError):
        return None

    if not isinstance(data, dict) or "error" in data:
        return None

    result = data.get("result")
    if not isinstance(result, list) or not result:
        return None

    first_result = result[0]
    if not isinstance(first_result, dict):
        return None
    infos = first_result.get("infos")
    if not isinstance(infos, list) or not infos:
        return None
    info = infos[0]
    if not isinstance(info, list):
        return None
    return info


def _extract_kbase_object_info_context(info: list[object]) -> tuple[str, str, str] | None:
    """Extract description context from a KBase workspace object info tuple."""
    object_name = info[1] if len(info) > 1 else None
    object_type = info[2] if len(info) > 2 else None
    metadata = info[10] if len(info) > 10 else None

    if not isinstance(metadata, dict):
        metadata = {}

    candidates: list[str] = []
    for key in ("description", "name", "title", "narrative_nice_name"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value)

    if isinstance(object_name, str) and object_name.strip():
        candidates.append(object_name)
    if isinstance(object_type, str) and object_type.startswith("KBaseNarrative.Narrative"):
        ws_name = metadata.get("ws_name")
        if isinstance(ws_name, str) and ws_name.strip():
            candidates.append(ws_name)

    for raw in candidates:
        cleaned = _clean_text(raw)
        if not cleaned:
            continue
        if _is_uninformative_kbase_text(cleaned):
            continue
        return cleaned, raw, "description"

    summary = _build_kbase_object_metadata_summary(object_name, object_type, metadata)
    if summary is not None:
        cleaned = _clean_text(summary)
        if cleaned:
            return cleaned, summary, "description"
    return None


def _is_uninformative_kbase_text(text: str) -> bool:
    """Return True for technical KBase labels that are weak LLM context."""
    lowered = text.lower().strip()
    if not lowered:
        return True
    if lowered.startswith("narrative."):
        return True
    if "__" in text and " " not in text:
        return True
    return False


def _build_kbase_object_metadata_summary(
    object_name: object, object_type: object, metadata: dict[str, object]
) -> str | None:
    """Build a short description from KBase object metadata for non-narrative refs."""
    if isinstance(object_type, str) and object_type.startswith("KBaseNarrative.Narrative"):
        return None

    summary_parts: list[str] = []
    for key in ("Size", "N Contigs", "GC content"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            summary_parts.append(f"{key}={value.strip()}")

    if not summary_parts:
        return None

    label = _humanize_kbase_object_name(object_name)
    type_text = (
        object_type.strip() if isinstance(object_type, str) and object_type.strip() else None
    )

    context_parts: list[str] = []
    if label:
        context_parts.append(label)
    else:
        context_parts.append("KBase object")
    if type_text:
        context_parts.append(f"type {type_text}")
    context_parts.append(f"metadata: {', '.join(summary_parts)}")
    return " ; ".join(context_parts)


def _humanize_kbase_object_name(object_name: object) -> str | None:
    """Convert KBase object names with separators into readable labels."""
    if not isinstance(object_name, str):
        return None
    stripped = object_name.strip()
    if not stripped:
        return None
    if stripped.lower().startswith("narrative."):
        return None

    humanized = re.sub(r"[_\-.]+", " ", stripped)
    humanized = re.sub(r"\s+", " ", humanized).strip()
    return humanized or None


def _collect_massive_accession_candidates(doi: str) -> list[str]:
    """Collect likely ProteomeXchange/MassIVE dataset accessions for a DOI."""
    candidates: list[str] = []
    seen: set[str] = set()

    direct = _extract_massive_accessions(doi)
    for accession in direct:
        if accession not in seen:
            seen.add(accession)
            candidates.append(accession)

    location = _resolve_doi_redirect_location(doi)
    if location is not None:
        for accession in _extract_massive_accessions(location):
            if accession not in seen:
                seen.add(accession)
                candidates.append(accession)

    return candidates


def _resolve_doi_redirect_location(doi: str) -> str | None:
    """Resolve DOI redirect location without following downstream redirects."""
    try:
        response = requests.get(
            f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException:
        return None

    if response.status_code not in {301, 302, 303, 307, 308}:
        return None

    location = response.headers.get("Location")
    if not isinstance(location, str) or not location.strip():
        return None
    return location


def _extract_massive_accessions(text: str) -> list[str]:
    """Extract unique ProteomeXchange/MassIVE accession tokens from text."""
    matches = re.findall(r"(PXD\d{6,}|MSV\d{6,})", text.upper())
    seen: set[str] = set()
    accessions: list[str] = []
    for match in matches:
        if match in seen:
            continue
        seen.add(match)
        accessions.append(match)
    return accessions


def _search_proxi_accessions_by_doi(doi: str) -> list[str]:
    """Search PROXI dataset rows for publication cells mentioning the DOI."""
    query_values = [doi, f"https://doi.org/{doi}", f"doi:{doi}"]
    seen: set[str] = set()
    accessions: list[str] = []

    for publication_query in query_values:
        rows = _fetch_proxi_dataset_rows(publication_query)
        for row in rows:
            accession = _extract_proxi_row_accession_for_doi(row, doi)
            if accession is None or accession in seen:
                continue
            seen.add(accession)
            accessions.append(accession)

    return accessions


def _fetch_proxi_dataset_rows(publication_query: str) -> list[list[object]]:
    """Return PROXI dataset table rows for a publication-filtered query."""
    try:
        response = requests.get(
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
            return []
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []

    datasets = payload.get("datasets")
    if not isinstance(datasets, list):
        return []

    rows: list[list[object]] = []
    for row in datasets:
        if isinstance(row, list):
            rows.append(row)
    return rows


def _extract_proxi_row_accession_for_doi(row: list[object], doi: str) -> str | None:
    """Return a dataset accession if the PROXI result row references the DOI."""
    if not row:
        return None

    accession = row[0] if len(row) > 0 else None
    publication_cell = row[7] if len(row) > 7 else None
    if not isinstance(accession, str):
        return None
    if not isinstance(publication_cell, str):
        return None
    if not _text_mentions_doi(publication_cell, doi):
        return None

    cleaned_accession = accession.strip().upper()
    if not cleaned_accession:
        return None
    return cleaned_accession


def _fetch_proxi_dataset(accession: str) -> dict[str, object] | None:
    """Fetch a PROXI dataset detail document by accession/identifier."""
    try:
        response = requests.get(
            f"{PROXI_DATASETS_API}/{accession}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(payload, dict):
        return None

    status = payload.get("status")
    if isinstance(status, str) and status.lower() == "error":
        return None
    return payload


def _extract_massive_context(payload: dict[str, object]) -> tuple[str, str, str] | None:
    """Extract MassIVE context from PROXI dataset details."""
    for key in ("description", "datasetSummary"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned, value, "description"

    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        cleaned = _clean_text(title)
        if cleaned:
            return cleaned, title, "description"
    return None


def _extract_massive_context_from_datacite_titles(doi: str) -> tuple[str, str, str] | None:
    """Fallback to DataCite title metadata when MassIVE APIs do not return context."""
    try:
        response = requests.get(
            f"{DATACITE_API}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        attrs = response.json().get("data", {}).get("attributes", {})
    except (requests.RequestException, ValueError):
        return None

    titles = attrs.get("titles")
    if not isinstance(titles, list):
        return None

    subtitle_candidate: str | None = None
    title_candidate: str | None = None
    for item in titles:
        if not isinstance(item, dict):
            continue
        raw_title = item.get("title")
        if not isinstance(raw_title, str) or not raw_title.strip():
            continue

        title_type = item.get("titleType")
        if isinstance(title_type, str) and title_type.lower() == "subtitle":
            if subtitle_candidate is None:
                subtitle_candidate = raw_title
        elif title_candidate is None:
            title_candidate = raw_title

    for raw in (subtitle_candidate, title_candidate):
        if raw is None:
            continue
        cleaned = _clean_text(raw)
        if cleaned:
            return cleaned, raw, "description"
    return None


def _search_cyverse_metadata_for_doi(doi: str) -> list[dict[str, object]]:
    """Search CyVerse metadata AVUs for records containing the DOI value."""
    try:
        response = requests.post(
            CYVERSE_METADATA_SEARCH_API,
            json={"value": [doi]},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return []
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []
    return _extract_cyverse_avu_list(payload)


def _fetch_cyverse_target_metadata(target_id: str) -> list[dict[str, object]]:
    """Return metadata AVUs attached to a CyVerse target UUID."""
    try:
        response = requests.get(
            CYVERSE_METADATA_API,
            params={"target-id": target_id},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return []
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []
    return _extract_cyverse_avu_list(payload)


def _extract_cyverse_avu_list(payload: object) -> list[dict[str, object]]:
    """Normalize CyVerse AVU response payloads into a list of AVU dicts."""
    if isinstance(payload, dict):
        avus = payload.get("avus")
        if isinstance(avus, list):
            return [item for item in avus if isinstance(item, dict)]
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _extract_cyverse_target_ids(avus: list[dict[str, object]]) -> list[str]:
    """Collect unique target UUIDs from CyVerse AVU entries."""
    seen: set[str] = set()
    target_ids: list[str] = []
    for avu in _iter_cyverse_avus(avus):
        value = avu.get("target_id")
        if not isinstance(value, str):
            continue
        target_id = value.strip()
        if not target_id or target_id in seen:
            continue
        seen.add(target_id)
        target_ids.append(target_id)
    return target_ids


def _extract_cyverse_context(
    avus: list[dict[str, object]], requested_doi: str
) -> tuple[str, str, str] | None:
    """Choose best CyVerse metadata text from abstract/description-like AVUs."""
    best_rank = 99
    best_length = -1
    best_context: tuple[str, str, str] | None = None

    for avu in _iter_cyverse_avus(avus):
        attr = avu.get("attr")
        raw_value = avu.get("value")
        if not isinstance(attr, str) or not isinstance(raw_value, str):
            continue

        cleaned = _clean_text(raw_value)
        if not cleaned or _cyverse_value_is_requested_doi(cleaned, requested_doi):
            continue

        ranked = _rank_cyverse_context_attr(attr)
        if ranked is None:
            continue
        rank, kind = ranked

        if rank < best_rank or (rank == best_rank and len(cleaned) > best_length):
            best_rank = rank
            best_length = len(cleaned)
            best_context = cleaned, raw_value, kind

    return best_context


def _iter_cyverse_avus(avus: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return AVUs and nested AVUs as a flattened list."""
    flattened: list[dict[str, object]] = []
    for avu in avus:
        flattened.append(avu)
        nested = avu.get("avus")
        if not isinstance(nested, list):
            continue
        nested_avus = [item for item in nested if isinstance(item, dict)]
        if nested_avus:
            flattened.extend(_iter_cyverse_avus(nested_avus))
    return flattened


def _rank_cyverse_context_attr(attr: str) -> tuple[int, str] | None:
    """Map CyVerse metadata attribute names to context extraction priority."""
    lowered = attr.strip().lower()
    if not lowered:
        return None

    if "abstract" in lowered:
        return 0, "abstract"
    if "description" in lowered:
        return 1, "description"
    if "summary" in lowered:
        return 2, "description"
    if "title" in lowered:
        return 3, "description"
    if "note" in lowered or "comment" in lowered:
        return 4, "description"
    return None


def _cyverse_value_is_requested_doi(value: str, requested_doi: str) -> bool:
    """Return True when a CyVerse metadata value is just the DOI identifier."""
    lowered = value.strip().lower()
    doi_value = requested_doi.lower()
    if lowered in {
        doi_value,
        f"doi:{doi_value}",
        f"https://doi.org/{doi_value}",
        f"http://doi.org/{doi_value}",
    }:
        return True
    return False


def _text_mentions_doi(text: str, requested_doi: str) -> bool:
    """Return True when text appears to reference the requested DOI."""
    lowered = text.lower()
    doi_variants = {
        requested_doi.lower(),
        f"doi:{requested_doi.lower()}",
        f"https://doi.org/{requested_doi.lower()}",
        f"http://doi.org/{requested_doi.lower()}",
    }
    return any(variant in lowered for variant in doi_variants)


def _try_figshare(doi: str) -> str | None:
    """Return Figshare context text for article/collection DOI if present."""
    context = _try_figshare_entity_lookup(doi, FIGSHARE_API)
    if context:
        return context

    context = _try_figshare_entity_lookup(doi, FIGSHARE_COLLECTIONS_API)
    if context:
        return context
    return None


def _try_figshare_entity_lookup(doi: str, endpoint: str) -> str | None:
    """Lookup a Figshare entity list by DOI and extract context, including detail fetch."""
    try:
        response = requests.get(
            endpoint,
            params={"doi": doi},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None
    return _extract_figshare_context_with_detail(payload, endpoint)


def _extract_figshare_context_with_detail(payload: object, endpoint: str) -> str | None:
    """Extract context from Figshare search payload, preferring detailed entity records."""
    if isinstance(payload, dict):
        candidates: list[dict[str, object]] = [payload]
    elif isinstance(payload, list):
        candidates = [item for item in payload if isinstance(item, dict)]
    else:
        candidates = []

    for candidate in candidates:
        detail = _fetch_figshare_detail(candidate, endpoint)
        if detail is not None:
            context = _extract_figshare_text(detail)
            if context:
                return context

        context = _extract_figshare_text(candidate)
        if context:
            return context
    return None


def _fetch_figshare_detail(candidate: dict[str, object], endpoint: str) -> dict[str, object] | None:
    """Fetch a detailed Figshare entity record when search payload is summary-only."""
    detail_url = candidate.get("url_public_api")
    if not isinstance(detail_url, str) or not detail_url.strip():
        candidate_id = candidate.get("id")
        if isinstance(candidate_id, int) or (
            isinstance(candidate_id, str) and candidate_id.strip().isdigit()
        ):
            detail_url = f"{endpoint}/{candidate_id}"
        else:
            return None

    try:
        response = requests.get(
            detail_url,
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    if isinstance(payload, dict):
        return payload
    return None


def _extract_figshare_text(record: dict[str, object]) -> str | None:
    """Extract best available context text from a Figshare entity record."""
    for key in ("description", "resource_title", "title", "citation"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned
    return None


def _try_zenodo(doi: str) -> str | None:
    """Return Zenodo description text for a DOI if present."""
    try:
        response = requests.get(
            ZENODO_API,
            params={"q": f'doi:"{doi}"'},
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    hits = payload.get("hits", {}).get("hits", [])
    if not isinstance(hits, list):
        return None

    for hit in hits:
        if not isinstance(hit, dict):
            continue
        metadata = hit.get("metadata", {})
        if not isinstance(metadata, dict):
            continue
        description = metadata.get("description")
        if isinstance(description, str):
            cleaned = _clean_text(description)
            if cleaned:
                return cleaned
    return None


def _try_datacite(doi: str) -> tuple[str, str, str, str | None] | None:
    """Return cleaned/raw text from DataCite descriptions, preferring abstract."""
    try:
        response = requests.get(
            f"{DATACITE_API}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        attrs = response.json().get("data", {}).get("attributes", {})
        descriptions = attrs.get("descriptions")
        publisher = attrs.get("publisher")
        if not isinstance(descriptions, list):
            return None
    except (requests.RequestException, ValueError):
        return None

    preferred = _pick_datacite_description(descriptions)
    if preferred is None:
        return None
    raw, kind = preferred
    cleaned = _clean_text(raw)
    if not cleaned:
        return None
    return cleaned, raw, kind, publisher if isinstance(publisher, str) else None


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


def _try_crossref(doi: str) -> tuple[str, str, str, str | None] | None:
    """Return Crossref abstract text and publisher metadata if available."""
    try:
        response = requests.get(
            f"{CROSSREF_API}/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        message = response.json().get("message", {})
    except (requests.RequestException, ValueError):
        return None

    raw = message.get("abstract")
    publisher = message.get("publisher")
    if not isinstance(raw, str) or not raw.strip():
        return None
    cleaned = strip_jats_xml(raw)
    if not cleaned:
        return None
    return cleaned, raw, "abstract", publisher if isinstance(publisher, str) else None


def _try_content_negotiation(doi: str) -> tuple[str, str, str] | None:
    """Return context from CSL JSON content negotiation (abstract then note)."""
    try:
        response = requests.get(
            f"{DOI_CONTENT_NEGOTIATION_API}/{doi}",
            headers={
                "Accept": "application/vnd.citationstyles.csl+json",
                "User-Agent": USER_AGENT,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    abstract = payload.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        cleaned = strip_jats_xml(abstract)
        if cleaned:
            return cleaned, abstract, "abstract"

    note = payload.get("note")
    if isinstance(note, str) and note.strip():
        cleaned = _clean_text(note)
        if cleaned:
            return cleaned, note, "description"
    return None


def _find_first_text(payload: object, keys: tuple[str, ...]) -> tuple[str, str] | None:
    """Recursively find the first non-empty string value for target keys."""
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return key, value
        for value in payload.values():
            found = _find_first_text(value, keys)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_first_text(item, keys)
            if found is not None:
                return found
    return None


def _extract_first_xml_text(root: ET.Element, tags: set[str]) -> str | None:
    """Extract text from the first XML element whose localname is in tags."""
    for element in root.iter():
        if _local_name(element.tag) not in tags:
            continue
        raw = "".join(element.itertext())
        cleaned = _clean_text(raw)
        if cleaned:
            return cleaned
    return None


def _local_name(tag: str) -> str:
    """Return XML localname from namespaced tags."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    if ":" in tag:
        return tag.split(":", 1)[1]
    return tag


def _clean_text(text: str) -> str:
    """Normalize context text by stripping markup, entities, and extra whitespace."""
    cleaned = strip_jats_xml(text)
    cleaned = re.sub(r"\s+", " ", html.unescape(cleaned)).strip()
    return cleaned
