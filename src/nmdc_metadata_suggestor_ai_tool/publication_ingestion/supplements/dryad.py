"""Dryad datasets, fetched through their Zenodo mirror.

Dryad's own file-download API requires an OAuth bearer token and its
``file_stream`` route is Cloudflare-gated, so content is not retrievable
unauthenticated. When no Zenodo mirror exists we still list what Dryad holds,
marked download-gated, rather than issuing downloads that would all 401.
"""

from collections.abc import Iterable
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_TIMEOUT,
    DRYAD_API_URL,
    DRYAD_DOI_PREFIX,
    SUPPLEMENT_MAX_ARCHIVE_BYTES,
    SUPPLEMENT_MAX_FILE_BYTES,
    SUPPLEMENT_MAX_FILES,
    SUPPLEMENT_MAX_TEXT_CHARS,
    SUPPLEMENT_MAX_TOTAL_BYTES,
    USER_AGENT,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    normalize_doi,
    request_with_retry,
)
from nmdc_metadata_suggestor_ai_tool.file_kinds import DEFAULT_USEFUL_KINDS
from nmdc_metadata_suggestor_ai_tool.models.supplement import (
    SupplementFile,
    SupplementKind,
    SupplementRetrievalResult,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.shared import (
    SupplementCaps,
    apply_selection,
    classify_supplement,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.zenodo import (
    zenodo_file_members,
)


def is_dryad_doi(doi: str) -> bool:
    """Return True when *doi* is a Dryad dataset DOI (prefix 10.5061)."""
    return normalize_doi(doi).startswith(f"{DRYAD_DOI_PREFIX}/")


def retrieve_supplements_from_dryad(
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
    """Retrieve high-value files for a Dryad dataset DOI, via the Zenodo mirror.

    Dryad's own file-download API requires an OAuth bearer token (HTTP 401) and
    its ``file_stream`` route is Cloudflare-gated, so file *content* is not
    retrievable unauthenticated. Dryad datasets are mirrored on Zenodo, so we
    fetch content from there. When no mirror exists, the Dryad file listing is
    returned as ``skipped`` (marked download-gated) rather than failing to fetch.
    """
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
    result = SupplementRetrievalResult(doi=doi, source="dryad", attempts=["dryad"])

    # Primary path: fetch content from the Zenodo mirror of the Dryad dataset.
    members, _mirror_error = zenodo_file_members(doi, caps.max_file_bytes)
    if members:
        result.attempts.append("zenodo-mirror")
        apply_selection(result, members, caps, None, "Dryad mirror contained no high-value files")
        return result

    # No mirror: list the Dryad files but flag them as download-gated instead of
    # attempting the auth-gated downloads (which would 401 for every file).
    return dryad_list_only(doi, result)


def dryad_list_only(doi: str, result: SupplementRetrievalResult) -> SupplementRetrievalResult:
    """Populate *result.skipped* with Dryad's file listing, marked download-gated."""
    try:
        dataset = dryad_get(f"/api/v2/datasets/{quote(f'doi:{doi}', safe='')}")
        version_href = dataset["_links"]["stash:version"]["href"]
        files_doc = dryad_get(f"{version_href}/files")
        items = files_doc.get("_embedded", {}).get("stash:files", [])
    except Exception as exc:
        result.error = f"Dryad lookup failed: {exc}"
        return result

    if not items:
        result.error = "Dryad dataset reported no files"
        return result

    for item in items:
        path = item.get("path")
        if not path:
            continue
        result.skipped.append(
            SupplementFile(
                filename=path,
                kind=classify_supplement(path),
                source="dryad",
                size_bytes=int(item.get("size") or 0),
                skipped_reason="download gated (Dryad requires auth; no open Zenodo mirror)",
            )
        )
    result.error = "Dryad content not retrievable (auth-gated) and no open mirror found"
    return result


def dryad_url(path: str) -> str:
    """Resolve a host-absolute Dryad path (``/api/v2/...``) to a full URL."""
    parts = urlsplit(DRYAD_API_URL)
    return urljoin(f"{parts.scheme}://{parts.netloc}", path)


def dryad_get(path: str) -> dict[str, Any]:
    """GET a host-absolute Dryad API path and return parsed JSON."""
    response = request_with_retry(
        "GET",
        dryad_url(path),
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    return data
