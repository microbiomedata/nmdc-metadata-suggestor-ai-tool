"""Figshare article and collection files, by DOI."""

from collections.abc import Iterable
from typing import Any

from nmdc_metadata_suggestor_ai_tool.constants import (
    SUPPLEMENT_MAX_ARCHIVE_BYTES,
    SUPPLEMENT_MAX_FILE_BYTES,
    SUPPLEMENT_MAX_FILES,
    SUPPLEMENT_MAX_TEXT_CHARS,
    SUPPLEMENT_MAX_TOTAL_BYTES,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import normalize_doi
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.figshare import (
    FIGSHARE_ENDPOINTS,
    figshare_detail,
    figshare_search,
)
from nmdc_metadata_suggestor_ai_tool.file_kinds import DEFAULT_USEFUL_KINDS
from nmdc_metadata_suggestor_ai_tool.models.supplement import (
    SupplementKind,
    SupplementRetrievalResult,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.shared import (
    Member,
    SupplementCaps,
    apply_selection,
    first_matching,
    url_reader,
)


def retrieve_supplements_from_figshare(
    doi: str,
    *,
    useful_kinds: Iterable[SupplementKind] = DEFAULT_USEFUL_KINDS,
    max_files: int = SUPPLEMENT_MAX_FILES,
    max_file_bytes: int = SUPPLEMENT_MAX_FILE_BYTES,
    max_total_bytes: int = SUPPLEMENT_MAX_TOTAL_BYTES,
    max_archive_bytes: int = SUPPLEMENT_MAX_ARCHIVE_BYTES,
    max_text_chars: int = SUPPLEMENT_MAX_TEXT_CHARS,
    save_dir: str | None = None,
) -> SupplementRetrievalResult:
    """Retrieve high-value files from a Figshare article/collection DOI."""
    caps = SupplementCaps(
        useful_kinds=frozenset(useful_kinds),
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        max_archive_bytes=max_archive_bytes,
        max_text_chars=max_text_chars,
        save_dir=save_dir,
    )
    doi = normalize_doi(doi)
    result = SupplementRetrievalResult(doi=doi, source="figshare", attempts=["figshare"])

    detail = figshare_detail_for_doi(doi)
    if detail is None:
        result.error = "No Figshare record found for DOI"
        return result

    members: list[Member] = []
    for file_entry in detail.get("files", []):
        name = file_entry.get("name")
        url = file_entry.get("download_url")
        if not name or not url:
            continue
        members.append(
            Member(
                name=name,
                size=int(file_entry.get("size") or 0),
                read=url_reader(url, caps.max_file_bytes),
            )
        )

    apply_selection(result, members, caps, None, "Figshare record contained no high-value files")
    return result


def figshare_detail_for_doi(doi: str) -> dict[str, Any] | None:
    """Resolve a Figshare DOI to a detailed record (with a ``files`` list).

    The article/collection search and the follow-up detail fetch are the DOI
    resolver's; what is specific here is picking the DOI-matching record and
    requiring one that actually carries files.
    """
    for endpoint in FIGSHARE_ENDPOINTS:
        matches = figshare_search(doi, endpoint)
        if not isinstance(matches, list) or not matches:
            continue
        # The search is filtered by DOI, but if the endpoint returns several
        # entries pick the one whose DOI matches rather than guessing the first.
        candidate = first_matching(matches, doi, ("doi",))
        if candidate is None:
            continue
        detail = figshare_detail(candidate, endpoint)
        if isinstance(detail, dict) and detail.get("files"):
            return detail
    return None
