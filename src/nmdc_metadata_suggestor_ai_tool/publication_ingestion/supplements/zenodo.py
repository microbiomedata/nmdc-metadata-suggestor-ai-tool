"""Zenodo record files, by DOI or concept DOI.

:func:`zenodo_file_members` is also used by the Dryad retriever: Dryad datasets
are mirrored on Zenodo, and Dryad's own download API is auth-gated.
"""

from collections.abc import Iterable

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_TIMEOUT,
    SUPPLEMENT_MAX_ARCHIVE_BYTES,
    SUPPLEMENT_MAX_FILE_BYTES,
    SUPPLEMENT_MAX_FILES,
    SUPPLEMENT_MAX_TEXT_CHARS,
    SUPPLEMENT_MAX_TOTAL_BYTES,
    USER_AGENT,
    ZENODO_API,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    normalize_doi,
    request_with_retry,
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


def retrieve_supplements_from_zenodo(
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
    """Retrieve high-value files from a Zenodo record (by DOI or concept DOI)."""
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
    result = SupplementRetrievalResult(doi=doi, source="zenodo", attempts=["zenodo"])

    members, error = zenodo_file_members(doi, caps.max_file_bytes)
    if not members:
        result.error = error or "Zenodo record contained no files"
        return result

    apply_selection(result, members, caps, None, "Zenodo record contained no high-value files")
    return result


def zenodo_file_members(doi: str, max_file_bytes: int) -> tuple[list[Member], str | None]:
    """Return download members for the Zenodo record matching *doi*.

    Shared by the Zenodo retriever and the Dryad retriever (Dryad datasets are
    mirrored on Zenodo, and Dryad's own download API is auth-gated). Returns
    ``(members, error)`` where ``error`` is set when no record/files were found.
    """
    try:
        response = request_with_retry(
            "GET",
            ZENODO_API,
            params={"q": f'doi:"{doi}" OR conceptdoi:"{doi}"'},
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        hits = response.json().get("hits", {}).get("hits", [])
    except Exception as exc:
        return [], f"Zenodo lookup failed: {exc}"

    hit = first_matching(hits, doi, ("doi", "conceptdoi"))
    if hit is None:
        return [], "No Zenodo record found for DOI"

    members: list[Member] = []
    for file_entry in hit.get("files", []):
        name = file_entry.get("key") or file_entry.get("filename")
        links = file_entry.get("links", {})
        # Link key varies by Zenodo API generation: InvenioRDM exposes the file
        # content under "content", legacy Zenodo under "self", older under "download".
        url = links.get("content") or links.get("self") or links.get("download")
        if not name or not url:
            continue
        members.append(
            Member(
                name=name,
                size=int(file_entry.get("size") or 0),
                read=url_reader(url, max_file_bytes),
            )
        )
    return members, None
