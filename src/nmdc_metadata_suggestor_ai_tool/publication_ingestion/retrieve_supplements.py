"""Retrieve supplementary materials for a manuscript (or dataset) DOI.

The goal is to give the NMDC metadata suggestor extra context for characterizing
submissions and samples -- not to comprehensively mirror every supplement. This
module therefore favors *performance over completeness*:

* Only high-value file kinds (tabular data and documents; see
  :class:`~nmdc_metadata_suggestor_ai_tool.models.supplement.SupplementKind`)
  are kept by default.
* Downloads are bounded by size/count caps and are never retried beyond
  ``request_with_retry``'s transient-error handling. If supplements are hard to
  obtain, we give up quickly rather than hammering the source.

Sources, tried in order by :func:`retrieve_supplements`:

1. **Europe PMC** ``supplementaryFiles`` -- a ZIP of an open-access article's
   supplements. Captions/labels are pulled from the article's JATS full text so
   files can be selected by content, not just extension.
2. **NCBI PMC OA Web Service** -- a fallback sibling that fetches the ``.tar.gz``
   OA package when Europe PMC reports no supplements. Captions come from the
   ``.nxml`` inside the package.
3. **Dryad** -- for data-repository DOIs (prefix ``10.5061``), lists and fetches
   the deposited files (often clean tabular sample metadata).

For publisher-hosted supplements behind Cloudflare/JS challenges, prefer the
agentic ``web_fetch`` path documented in the ``supplement-retrieval`` skill.
"""

import io
import logging
import os
import re
import tarfile
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple
from urllib.parse import quote, urljoin, urlsplit

from nmdc_metadata_suggestor_ai_tool.constants import (
    CROSSREF_API_URL,
    DATACITE_API_URL,
    DEFAULT_TIMEOUT,
    DRYAD_API_URL,
    DRYAD_DOI_PREFIX,
    EUROPEPMC_API_URL,
    EUROPEPMC_FULLTEXT_XML_URL_TEMPLATE,
    EUROPEPMC_SUPPL_URL_TEMPLATE,
    FIGSHARE_API,
    FIGSHARE_COLLECTIONS_API,
    PMC_OA_SERVICE_URL,
    SUPPLEMENT_MAX_ARCHIVE_BYTES,
    SUPPLEMENT_MAX_FILE_BYTES,
    SUPPLEMENT_MAX_FILES,
    SUPPLEMENT_MAX_TEXT_CHARS,
    SUPPLEMENT_MAX_TOTAL_BYTES,
    SUPPLEMENT_MAX_XML_BYTES,
    UNSAFE_XML_DECLARATION_PATTERN,
    USER_AGENT,
    ZENODO_API,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    normalize_doi,
    request_with_retry,
)
from nmdc_metadata_suggestor_ai_tool.file_kinds import (
    DEFAULT_USEFUL_KINDS,
    TEXT_LIKE_EXTENSIONS,
    classify_file,
    file_extension,
)
from nmdc_metadata_suggestor_ai_tool.models.supplement import (
    SupplementFile,
    SupplementKind,
    SupplementRetrievalResult,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import remove_temp_file

logger = logging.getLogger(__name__)

# Backwards-compatible alias: the file-type taxonomy now lives in ``file_kinds``.
# ``classify_supplement`` remains the documented name used by the skill/tests.
classify_supplement = classify_file


def find_supplement_source_europepmc(doi: str) -> dict[str, object]:
    """Locate an article in Europe PMC and report supplement availability.

    This performs a single metadata lookup (no supplement download) so callers
    can cheaply decide whether retrieval is worthwhile.

    Args:
        doi: Digital Object Identifier in any common format.

    Returns:
        Dict with keys ``source``, ``article_id``, ``pmcid``, ``is_open_access``,
        ``has_supplements``, ``zip_url``, and optional ``error``.
    """
    doi = normalize_doi(doi)
    result: dict[str, object] = {
        "source": None,
        "article_id": None,
        "pmcid": None,
        "is_open_access": False,
        "has_supplements": False,
        "zip_url": None,
    }
    try:
        params = {"query": f'DOI:"{doi}"', "format": "json", "pageSize": 1}
        response = request_with_retry(
            "GET",
            EUROPEPMC_API_URL,
            params=params,
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        articles = response.json().get("resultList", {}).get("result", [])
    except Exception as exc:
        result["error"] = str(exc)
        return result

    if not articles:
        result["error"] = "No Europe PMC record found for DOI"
        return result

    article = articles[0]
    source = article.get("source")
    article_id = article.get("id")
    result["source"] = source
    result["article_id"] = article_id
    result["pmcid"] = article.get("pmcid")
    result["is_open_access"] = article.get("isOpenAccess") == "Y"
    # ``hasSuppl`` is "Y"/"N" when present. Treat only an explicit "Y" as a
    # signal to attempt download, so we avoid wasted requests (performance-first).
    result["has_supplements"] = article.get("hasSuppl") == "Y"
    if source and article_id:
        result["zip_url"] = EUROPEPMC_SUPPL_URL_TEMPLATE.format(
            source=source, article_id=article_id
        )
    return result


def _download_bounded(url: str, max_bytes: int) -> bytes:
    """Download *url* into memory, aborting if it exceeds *max_bytes*.

    Used for both archives (Europe PMC ZIP, PMC OA tarball) and single-file
    downloads (Zenodo/Figshare), so the raised messages stay source-agnostic.
    Uses a streamed read so an over-large body is never fully buffered. The
    ``Content-Length`` header (when present) short-circuits the download.

    Raises:
        RuntimeError: On HTTP error, oversized response, or network failure.
    """
    response = request_with_retry(
        "GET",
        url,
        stream=True,
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        response.raise_for_status()
        declared = response.headers.get("Content-Length")
        if declared is not None and declared.isdigit() and int(declared) > max_bytes:
            raise RuntimeError(f"Download size {declared} bytes exceeds cap {max_bytes} bytes")
        buffer = io.BytesIO()
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError(f"Download exceeded size cap of {max_bytes} bytes")
            buffer.write(chunk)
        return buffer.getvalue()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to download supplements: {exc}") from exc
    finally:
        response.close()


def _is_unsafe_member(name: str) -> bool:
    """Return True for archive member names that could escape the extraction dir."""
    if not name or name.endswith("/"):
        return True  # directory entry
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or ".." in normalized.split("/"):
        return True
    return False


def _materialize_member(
    kind: SupplementKind,
    filename: str,
    data: bytes,
    max_text_chars: int,
    save_dir: str | None,
) -> tuple[str | None, str | None]:
    """Turn a kept member's bytes into inline text or a saved temp file.

    Returns:
        ``(text, saved_path)`` -- exactly one is non-None.
    """
    if file_extension(filename) in TEXT_LIKE_EXTENSIONS:
        text = data.decode("utf-8", errors="replace")
        if len(text) > max_text_chars:
            text = text[:max_text_chars] + "\n...[truncated]"
        return text, None

    suffix = f".{file_extension(filename)}" if file_extension(filename) else ""
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        safe_name = os.path.basename(filename) or f"supplement{suffix}"
        path = os.path.join(save_dir, safe_name)
    else:
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
    with open(path, "wb") as handle:
        handle.write(data)
    return None, path


# ---------------------------------------------------------------------------
# JATS caption parsing (select supplements by content, not just extension)
# ---------------------------------------------------------------------------


def _localname(tag: str) -> str:
    """Return the XML localname, dropping any ``{namespace}`` prefix."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _caption_key(filename: str) -> str:
    """Normalize a filename/href to a match key (basename, no extension, lower)."""
    base = os.path.basename(filename).lower()
    stem, _, ext = base.rpartition(".")
    return stem if (ext and stem) else base


def parse_supplement_captions(xml_text: str | bytes) -> dict[str, str]:
    """Map supplement filenames to their captions from JATS full-text XML.

    Reads ``<supplementary-material>``/``<media>`` elements, keying each caption
    on the referenced file's normalized basename (see :func:`_caption_key`).

    Args:
        xml_text: JATS XML document (Europe PMC ``fullTextXML`` or a PMC nxml).

    Returns:
        Mapping of caption key -> caption text (empty on parse failure).
    """
    captions: dict[str, str] = {}
    try:
        text = xml_text if isinstance(xml_text, str) else xml_text.decode("utf-8")
    except UnicodeDecodeError:
        return captions

    # Refuse DOCTYPE/ENTITY declarations to avoid entity-expansion / injection via
    # untrusted XML, matching the guard used by the DOI resolvers.
    if UNSAFE_XML_DECLARATION_PATTERN.search(text):
        return captions

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return captions

    supplement_tags = {"supplementary-material", "inline-supplementary-material", "media"}
    for elem in root.iter():
        if _localname(elem.tag) not in supplement_tags:
            continue
        href = next(
            (v for k, v in elem.attrib.items() if _localname(k) == "href"),
            None,
        )
        if not href:
            continue
        text = re.sub(r"\s+", " ", " ".join(elem.itertext())).strip()
        key = _caption_key(href)
        if key and text and key not in captions:
            captions[key] = text[:1000]
    return captions


# ---------------------------------------------------------------------------
# Shared member selection: kind filter + caps + materialization + captions
# ---------------------------------------------------------------------------


class _Member(NamedTuple):
    """A candidate supplement file from any source."""

    name: str
    size: int  # reported (uncompressed) size; used for pre-read cap checks
    read: Callable[[], bytes]


def _zip_reader(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> Callable[[], bytes]:
    """Return a no-arg reader for a ZIP member (bound so it survives the loop)."""
    return lambda: archive.read(info)


def _tar_reader(tar: tarfile.TarFile, info: tarfile.TarInfo) -> Callable[[], bytes]:
    """Return a no-arg reader for a tar member."""
    return lambda: _read_tar_member(tar, info)


def _url_reader(url: str, max_bytes: int) -> Callable[[], bytes]:
    """Return a no-arg bounded downloader for a per-file URL (e.g. Dryad)."""
    return lambda: _download_bounded(url, max_bytes)


def _select_members(
    members: Iterable[_Member],
    *,
    kept_kinds: frozenset[SupplementKind],
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
    max_text_chars: int,
    save_dir: str | None,
    captions: dict[str, str] | None,
    source: str | None = None,
) -> tuple[list[SupplementFile], list[SupplementFile]]:
    """Filter candidate members to the high-value ones within caps.

    Returns ``(kept, skipped)``. Caps are checked against each member's reported
    size *before* reading, so oversized files are never downloaded/extracted, and
    re-checked against the actual byte count afterwards -- sources that report a
    zero/unknown ``size`` would otherwise slip past the budget.
    """
    kept: list[SupplementFile] = []
    skipped: list[SupplementFile] = []
    total_kept_bytes = 0

    def skip(name: str, kind: SupplementKind, size: int, reason: str) -> None:
        skipped.append(
            SupplementFile(
                filename=name,
                kind=kind,
                source=source,
                size_bytes=size,
                caption=_match_caption(name, captions),
                skipped_reason=reason,
            )
        )

    for member in members:
        name = member.name
        if _is_unsafe_member(name):
            continue
        kind = classify_supplement(name)
        size = member.size

        if kind not in kept_kinds:
            skip(name, kind, size, f"low-value kind ({kind.value})")
            continue
        if size > max_file_bytes:
            skip(name, kind, size, f"file exceeds {max_file_bytes} byte cap")
            continue
        if len(kept) >= max_files:
            skip(name, kind, size, f"max_files ({max_files}) reached")
            continue
        if total_kept_bytes + size > max_total_bytes:
            skip(name, kind, size, f"max_total_bytes ({max_total_bytes}) reached")
            continue

        try:
            data = member.read()
        except Exception as exc:
            logger.debug("Failed to read supplement member %s: %s", name, exc)
            skip(name, kind, size, "read error")
            continue

        # Re-check against what we actually read: a reported ``size`` of 0 (common
        # for HTTP sources that omit Content-Length) would otherwise bypass both
        # caps, and materializing first would put the bytes on disk regardless.
        actual = len(data)
        if actual > max_file_bytes:
            skip(name, kind, actual, f"file exceeds {max_file_bytes} byte cap")
            continue
        if total_kept_bytes + actual > max_total_bytes:
            skip(name, kind, actual, f"max_total_bytes ({max_total_bytes}) reached")
            continue

        text, saved_path = _materialize_member(kind, name, data, max_text_chars, save_dir)
        total_kept_bytes += actual
        kept.append(
            SupplementFile(
                filename=name,
                kind=kind,
                source=source,
                size_bytes=actual,
                caption=_match_caption(name, captions),
                text=text,
                saved_path=saved_path,
            )
        )

    return kept, skipped


def _match_caption(filename: str, captions: dict[str, str] | None) -> str | None:
    """Look up a caption for *filename* by its normalized key."""
    if not captions:
        return None
    return captions.get(_caption_key(filename))


class SupplementCaps(NamedTuple):
    """Bundle of the retrieval caps shared by every source."""

    useful_kinds: frozenset[SupplementKind]
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    max_archive_bytes: int
    max_text_chars: int
    save_dir: str | None


def _resolve_caps(
    useful_kinds: Iterable[SupplementKind],
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
    max_archive_bytes: int,
    max_text_chars: int,
    save_dir: str | None,
) -> SupplementCaps:
    return SupplementCaps(
        useful_kinds=frozenset(useful_kinds),
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        max_archive_bytes=max_archive_bytes,
        max_text_chars=max_text_chars,
        save_dir=save_dir,
    )


def _apply_selection(
    result: SupplementRetrievalResult,
    members: Iterable[_Member],
    caps: SupplementCaps,
    captions: dict[str, str] | None,
    empty_error: str,
) -> SupplementRetrievalResult:
    """Run the shared selector over *members* and populate *result*."""
    kept, skipped = _select_members(
        members,
        kept_kinds=caps.useful_kinds,
        max_files=caps.max_files,
        max_file_bytes=caps.max_file_bytes,
        max_total_bytes=caps.max_total_bytes,
        max_text_chars=caps.max_text_chars,
        save_dir=caps.save_dir,
        captions=captions,
        source=result.source,
    )
    result.files = kept
    result.skipped = skipped
    if not kept:
        result.error = empty_error
    return result


# ---------------------------------------------------------------------------
# Source 1: Europe PMC supplementaryFiles ZIP
# ---------------------------------------------------------------------------


def _fetch_europepmc_captions(source: str, article_id: str) -> dict[str, str]:
    """Best-effort fetch of supplement captions from Europe PMC full-text JATS.

    The JATS document is size-bounded like every other download: an over-large
    (or missing/non-200) document yields no captions rather than being buffered.
    """
    url = EUROPEPMC_FULLTEXT_XML_URL_TEMPLATE.format(source=source, article_id=article_id)
    try:
        data = _download_bounded(url, SUPPLEMENT_MAX_XML_BYTES)
    except RuntimeError:
        return {}
    return parse_supplement_captions(data)


def retrieve_supplements_from_europepmc(
    doi: str,
    *,
    useful_kinds: Iterable[SupplementKind] = DEFAULT_USEFUL_KINDS,
    max_files: int = SUPPLEMENT_MAX_FILES,
    max_file_bytes: int = SUPPLEMENT_MAX_FILE_BYTES,
    max_total_bytes: int = SUPPLEMENT_MAX_TOTAL_BYTES,
    max_archive_bytes: int = SUPPLEMENT_MAX_ARCHIVE_BYTES,
    max_text_chars: int = SUPPLEMENT_MAX_TEXT_CHARS,
    save_dir: str | None = None,
    include_captions: bool = True,
) -> SupplementRetrievalResult:
    """Retrieve high-value supplements for *doi* via Europe PMC.

    Text-like supplements (csv/tsv/txt) are inlined into ``SupplementFile.text``;
    other kept supplements are written to temp files (or ``save_dir``) and
    exposed via ``SupplementFile.saved_path``. Captions from the article's JATS
    full text are attached when available. Low-value kinds and files exceeding
    the caps are recorded in ``result.skipped`` rather than kept.

    Non-open-access articles are reported as an error without any download
    attempt: Europe PMC's ``supplementaryFiles`` endpoint is OA-scoped.

    Returns:
        A :class:`SupplementRetrievalResult`. ``error`` is set (and ``files``
        empty) when no supplements could be retrieved.
    """
    caps = _resolve_caps(
        useful_kinds,
        max_files,
        max_file_bytes,
        max_total_bytes,
        max_archive_bytes,
        max_text_chars,
        save_dir,
    )
    doi = normalize_doi(doi)
    result = SupplementRetrievalResult(doi=doi, source="europepmc", attempts=["europepmc"])

    located = find_supplement_source_europepmc(doi)
    result.pmcid = located.get("pmcid")  # type: ignore[assignment]
    if located.get("error"):
        result.error = str(located["error"])
        return result
    # The ``supplementaryFiles`` endpoint only serves open-access articles, so a
    # non-OA record can never yield a ZIP -- skip the request entirely.
    if not located.get("is_open_access"):
        result.error = "Article is not open access; Europe PMC serves no supplements for it"
        return result
    if not located.get("has_supplements") or not located.get("zip_url"):
        result.error = "No open-access supplementary files reported for DOI"
        return result

    try:
        archive_bytes = _download_bounded(str(located["zip_url"]), caps.max_archive_bytes)
    except RuntimeError as exc:
        result.error = str(exc)
        return result

    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile:
        result.error = "Supplement archive was not a valid ZIP"
        return result

    captions: dict[str, str] = {}
    if include_captions and located.get("source") and located.get("article_id"):
        captions = _fetch_europepmc_captions(str(located["source"]), str(located["article_id"]))

    with archive:
        members = [
            _Member(name=info.filename, size=info.file_size, read=_zip_reader(archive, info))
            for info in archive.infolist()
        ]
        _apply_selection(
            result, members, caps, captions, "Archive contained no high-value supplement files"
        )
    return result


# ---------------------------------------------------------------------------
# Source 2: NCBI PMC Open Access Web Service tar.gz package
# ---------------------------------------------------------------------------


def find_supplement_source_pmc_oa(pmcid: str) -> dict[str, object]:
    """Resolve a PMCID to its NCBI OA ``.tar.gz`` package URL (no download).

    Args:
        pmcid: PMC identifier, e.g. ``PMC3258517`` (a bare id is prefixed).

    Returns:
        Dict with ``pmcid``, ``tgz_url`` (https), and optional ``error``.
    """
    pmcid = pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
    result: dict[str, object] = {"pmcid": pmcid, "tgz_url": None}
    try:
        response = request_with_retry(
            "GET",
            PMC_OA_SERVICE_URL,
            params={"id": pmcid},
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception as exc:
        result["error"] = str(exc)
        return result

    error_elem = root.find(".//error")
    if error_elem is not None:
        result["error"] = error_elem.get("code") or (error_elem.text or "OA service error")
        return result

    for link in root.iter("link"):
        if link.get("format") == "tgz" and link.get("href"):
            # OA hrefs are ftp:// URLs; the same path is served over https.
            result["tgz_url"] = re.sub(r"^ftp://", "https://", link.get("href", ""))
            break
    if not result["tgz_url"]:
        result["error"] = "No OA tgz package available (article may not be in the OA subset)"
    return result


def retrieve_supplements_from_pmc_oa(
    pmcid: str,
    *,
    doi: str = "",
    useful_kinds: Iterable[SupplementKind] = DEFAULT_USEFUL_KINDS,
    max_files: int = SUPPLEMENT_MAX_FILES,
    max_file_bytes: int = SUPPLEMENT_MAX_FILE_BYTES,
    max_total_bytes: int = SUPPLEMENT_MAX_TOTAL_BYTES,
    max_archive_bytes: int = SUPPLEMENT_MAX_ARCHIVE_BYTES,
    max_text_chars: int = SUPPLEMENT_MAX_TEXT_CHARS,
    save_dir: str | None = None,
) -> SupplementRetrievalResult:
    """Retrieve high-value supplements from an NCBI PMC OA ``.tar.gz`` package.

    Captions are parsed from the ``.nxml`` bundled inside the package, so no
    extra request is needed to select supplements by content.
    """
    caps = _resolve_caps(
        useful_kinds,
        max_files,
        max_file_bytes,
        max_total_bytes,
        max_archive_bytes,
        max_text_chars,
        save_dir,
    )
    result = SupplementRetrievalResult(
        doi=normalize_doi(doi) if doi else pmcid, source="pmc_oa", pmcid=pmcid, attempts=["pmc_oa"]
    )

    located = find_supplement_source_pmc_oa(pmcid)
    result.pmcid = str(located.get("pmcid") or pmcid)
    if located.get("error") or not located.get("tgz_url"):
        result.error = str(located.get("error") or "No OA package URL resolved")
        return result

    try:
        archive_bytes = _download_bounded(str(located["tgz_url"]), caps.max_archive_bytes)
    except RuntimeError as exc:
        result.error = str(exc)
        return result

    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            captions = _captions_from_tar(tar)
            members = [
                _Member(name=info.name, size=info.size, read=_tar_reader(tar, info))
                for info in tar.getmembers()
                if info.isfile()
            ]
            _apply_selection(
                result, members, caps, captions, "OA package contained no high-value supplements"
            )
    except (tarfile.TarError, OSError) as exc:
        result.error = f"Failed to read OA package: {exc}"
    return result


def _read_tar_member(
    tar: tarfile.TarFile, member: tarfile.TarInfo, max_bytes: int | None = None
) -> bytes:
    """Read a regular-file member's bytes from an open tar archive.

    Raises:
        RuntimeError: If the member is not a regular file, or if *max_bytes* is
            given and the member's content exceeds it.
    """
    handle = tar.extractfile(member)
    if handle is None:
        raise RuntimeError("member is not a regular file")
    with handle:
        if max_bytes is None:
            return handle.read()
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise RuntimeError(f"member exceeded size cap of {max_bytes} bytes")
    return data


def _captions_from_tar(tar: tarfile.TarFile) -> dict[str, str]:
    """Parse supplement captions from the first ``.nxml`` in an OA package.

    OA packages are untrusted input, so the NXML is bounded by
    ``SUPPLEMENT_MAX_XML_BYTES`` -- the same cap the Europe PMC JATS fetch uses --
    before it is handed to the XML parser. An oversized NXML yields no captions
    rather than a large parse.
    """
    for member in tar.getmembers():
        if not (member.isfile() and member.name.lower().endswith(".nxml")):
            continue
        if member.size > SUPPLEMENT_MAX_XML_BYTES:
            logger.debug(
                "Skipping oversized NXML %s (%d bytes > %d cap)",
                member.name,
                member.size,
                SUPPLEMENT_MAX_XML_BYTES,
            )
            continue
        try:
            data = _read_tar_member(tar, member, max_bytes=SUPPLEMENT_MAX_XML_BYTES)
        except Exception:
            continue
        return parse_supplement_captions(data)
    return {}


# ---------------------------------------------------------------------------
# Source 3: Dryad data repository
# ---------------------------------------------------------------------------


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
    caps = _resolve_caps(
        useful_kinds,
        max_files,
        max_file_bytes,
        max_total_bytes,
        max_archive_bytes,
        max_text_chars,
        save_dir,
    )
    doi = normalize_doi(doi)
    result = SupplementRetrievalResult(doi=doi, source="dryad", attempts=["dryad"])

    # Primary path: fetch content from the Zenodo mirror of the Dryad dataset.
    members, _mirror_error = _zenodo_file_members(doi, caps.max_file_bytes)
    if members:
        result.attempts.append("zenodo-mirror")
        _apply_selection(result, members, caps, None, "Dryad mirror contained no high-value files")
        return result

    # No mirror: list the Dryad files but flag them as download-gated instead of
    # attempting the auth-gated downloads (which would 401 for every file).
    return _dryad_list_only(doi, result)


def _dryad_list_only(doi: str, result: SupplementRetrievalResult) -> SupplementRetrievalResult:
    """Populate *result.skipped* with Dryad's file listing, marked download-gated."""
    try:
        dataset = _dryad_get(f"/api/v2/datasets/{quote(f'doi:{doi}', safe='')}")
        version_href = dataset["_links"]["stash:version"]["href"]
        files_doc = _dryad_get(f"{version_href}/files")
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


def _dryad_url(path: str) -> str:
    """Resolve a host-absolute Dryad path (``/api/v2/...``) to a full URL."""
    parts = urlsplit(DRYAD_API_URL)
    return urljoin(f"{parts.scheme}://{parts.netloc}", path)


def _dryad_get(path: str) -> dict[str, Any]:
    """GET a host-absolute Dryad API path and return parsed JSON."""
    response = request_with_retry(
        "GET",
        _dryad_url(path),
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    return data


# ---------------------------------------------------------------------------
# Source 4: Zenodo
# ---------------------------------------------------------------------------


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
    caps = _resolve_caps(
        useful_kinds,
        max_files,
        max_file_bytes,
        max_total_bytes,
        max_archive_bytes,
        max_text_chars,
        save_dir,
    )
    doi = normalize_doi(doi)
    result = SupplementRetrievalResult(doi=doi, source="zenodo", attempts=["zenodo"])

    members, error = _zenodo_file_members(doi, caps.max_file_bytes)
    if not members:
        result.error = error or "Zenodo record contained no files"
        return result

    _apply_selection(result, members, caps, None, "Zenodo record contained no high-value files")
    return result


def _zenodo_file_members(doi: str, max_file_bytes: int) -> tuple[list[_Member], str | None]:
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

    hit = _first_matching(hits, doi, ("doi", "conceptdoi"))
    if hit is None:
        return [], "No Zenodo record found for DOI"

    members: list[_Member] = []
    for file_entry in hit.get("files", []):
        name = file_entry.get("key") or file_entry.get("filename")
        links = file_entry.get("links", {})
        # Link key varies by Zenodo API generation: InvenioRDM exposes the file
        # content under "content", legacy Zenodo under "self", older under "download".
        url = links.get("content") or links.get("self") or links.get("download")
        if not name or not url:
            continue
        members.append(
            _Member(
                name=name,
                size=int(file_entry.get("size") or 0),
                read=_url_reader(url, max_file_bytes),
            )
        )
    return members, None


# ---------------------------------------------------------------------------
# Source 5: Figshare
# ---------------------------------------------------------------------------


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
    caps = _resolve_caps(
        useful_kinds,
        max_files,
        max_file_bytes,
        max_total_bytes,
        max_archive_bytes,
        max_text_chars,
        save_dir,
    )
    doi = normalize_doi(doi)
    result = SupplementRetrievalResult(doi=doi, source="figshare", attempts=["figshare"])

    detail = _figshare_detail_for_doi(doi)
    if detail is None:
        result.error = "No Figshare record found for DOI"
        return result

    members: list[_Member] = []
    for file_entry in detail.get("files", []):
        name = file_entry.get("name")
        url = file_entry.get("download_url")
        if not name or not url:
            continue
        members.append(
            _Member(
                name=name,
                size=int(file_entry.get("size") or 0),
                read=_url_reader(url, caps.max_file_bytes),
            )
        )

    _apply_selection(result, members, caps, None, "Figshare record contained no high-value files")
    return result


def _figshare_detail_for_doi(doi: str) -> dict[str, Any] | None:
    """Resolve a Figshare DOI to a detailed record (with a ``files`` list)."""
    for endpoint in (FIGSHARE_API, FIGSHARE_COLLECTIONS_API):
        try:
            response = request_with_retry(
                "GET",
                endpoint,
                params={"doi": doi},
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            matches = response.json()
        except Exception:
            continue
        if not isinstance(matches, list) or not matches:
            continue
        # The search is filtered by DOI, but if the endpoint returns several
        # entries pick the one whose DOI matches rather than guessing the first.
        candidate = _first_matching(matches, doi, ("doi",))
        if candidate is None:
            continue
        detail_url = candidate.get("url_public_api") or f"{endpoint}/{candidate.get('id')}"
        try:
            detail_response = request_with_retry(
                "GET", detail_url, timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT}
            )
            detail_response.raise_for_status()
            detail = detail_response.json()
        except Exception:
            continue
        if isinstance(detail, dict) and detail.get("files"):
            return detail
    return None


def _first_matching(
    hits: list[dict[str, Any]], doi: str, keys: tuple[str, ...]
) -> dict[str, Any] | None:
    """Return the hit whose DOI in *keys* matches *doi*.

    Falls back to the sole hit only when the query returned exactly one, so an
    unrelated record is never guessed out of many results.
    """
    if not isinstance(hits, list) or not hits:
        return None
    target = doi.lower()
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        for key in keys:
            value = hit.get(key)
            if isinstance(value, str) and normalize_doi(value).lower() == target:
                return hit
    if len(hits) == 1 and isinstance(hits[0], dict):
        return hits[0]
    return None


# ---------------------------------------------------------------------------
# Related-dataset discovery from a publication's metadata
# ---------------------------------------------------------------------------

# DOI prefix -> the supplement source that can fetch that repository's files.
_REPO_BY_PREFIX: dict[str, str] = {
    DRYAD_DOI_PREFIX: "dryad",
    "10.5281": "zenodo",
    "10.6084": "figshare",
}

# Crossref relation types / DataCite relationTypes that point at *data* related
# to the article (as opposed to citations). Compared after lowercasing and
# stripping non-letters, so "IsSupplementedBy" and "is-supplemented-by" match.
_DATA_RELATION_TYPES: frozenset[str] = frozenset(
    {
        "issupplementedby",
        "haspart",
        "issourceof",
        "isderivedfrom",
        "isdocumentedby",
    }
)


def _normalize_relation(relation_type: str) -> str:
    return re.sub(r"[^a-z]", "", relation_type.lower())


def _repo_for_doi(doi: str) -> str | None:
    """Return the supplement source able to fetch *doi*, or None."""
    prefix = normalize_doi(doi).split("/", 1)[0]
    return _REPO_BY_PREFIX.get(prefix)


def find_related_data_dois(doi: str) -> list[str]:
    """Find data-repository DOIs linked from a publication's relation metadata.

    Reads Crossref ``relation`` and DataCite ``relatedIdentifiers``, keeps entries
    whose relation indicates associated data (see ``_DATA_RELATION_TYPES``) and
    whose target is a DOI we can fetch (Dryad/Zenodo/Figshare).

    Returns:
        Deduplicated, normalized data-repository DOIs (empty on any failure).
    """
    doi = normalize_doi(doi)
    found: list[str] = []
    found.extend(_crossref_related_dois(doi))
    found.extend(_datacite_related_dois(doi))

    unique: list[str] = []
    for candidate in found:
        normalized = normalize_doi(candidate)
        if _repo_for_doi(normalized) and normalized not in unique and normalized != doi:
            unique.append(normalized)
    return unique


def _crossref_related_dois(doi: str) -> list[str]:
    """Extract related DOIs from a Crossref work's ``relation`` block."""
    try:
        response = request_with_retry(
            "GET",
            f"{CROSSREF_API_URL}/{doi}",
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        relation = response.json().get("message", {}).get("relation", {})
    except Exception:
        return []

    dois: list[str] = []
    if isinstance(relation, dict):
        for relation_type, entries in relation.items():
            if _normalize_relation(str(relation_type)) not in _DATA_RELATION_TYPES:
                continue
            for entry in entries if isinstance(entries, list) else []:
                if isinstance(entry, dict) and entry.get("id-type") == "doi":
                    identifier = entry.get("id")
                    if isinstance(identifier, str):
                        dois.append(identifier)
    return dois


def _datacite_related_dois(doi: str) -> list[str]:
    """Extract related DOIs from a DataCite record's ``relatedIdentifiers``."""
    try:
        response = request_with_retry(
            "GET",
            f"{DATACITE_API_URL}/{doi}",
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        attributes = response.json().get("data", {}).get("attributes", {})
        related = attributes.get("relatedIdentifiers", [])
    except Exception:
        return []

    dois: list[str] = []
    for entry in related if isinstance(related, list) else []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("relatedIdentifierType", "")).lower() != "doi":
            continue
        if _normalize_relation(str(entry.get("relationType", ""))) not in _DATA_RELATION_TYPES:
            continue
        identifier = entry.get("relatedIdentifier")
        if isinstance(identifier, str):
            dois.append(identifier)
    return dois


# ---------------------------------------------------------------------------
# Text / accession mining (fallback when relation metadata is absent)
# ---------------------------------------------------------------------------

# Repository-specific DOI shapes. Matching the exact form (rather than a generic
# DOI regex + prefix filter) stops trailing prose from riding along on a mined DOI
# -- e.g. "10.5281/zenodo.123and" yields "10.5281/zenodo.123", not the whole run.
_REPO_DOI_PATTERN = re.compile(
    r"10\.5281/zenodo\.\d+"  # Zenodo
    r"|10\.6084/m9\.figshare\.\d+(?:\.v\d+)?"  # Figshare (optional version)
    r"|10\.5061/dryad\.[a-z0-9]+(?:\.\d+)?",  # Dryad (optional version)
    re.IGNORECASE,
)

# Common sequence/proteomics repository accession patterns. Detected and surfaced
# for awareness, but NOT retrieved (they point at raw omics data, out of scope).
_ACCESSION_PATTERN = re.compile(
    r"\b("
    r"PRJ[EDN][A-Z]\d+"  # BioProject
    r"|SAM[EDN][A-Z]?\d+"  # BioSample
    r"|[SED]R[RXPS]\d+"  # SRA/ENA runs, experiments, projects, samples
    r"|GSE\d+|GSM\d+"  # GEO
    r"|PXD\d+|MSV\d{9}"  # ProteomeXchange / MassIVE
    r")\b"
)


def extract_dataset_dois_from_text(text: str) -> list[str]:
    """Return data-repository DOIs (Dryad/Zenodo/Figshare) mentioned in *text*.

    Uses repository-specific DOI shapes so trailing text can't be captured as
    part of a matched DOI. DOIs are lowercased (canonical for these repositories).
    """
    found: list[str] = []
    for match in _REPO_DOI_PATTERN.finditer(text):
        candidate = normalize_doi(match.group(0)).lower()
        if candidate not in found:
            found.append(candidate)
    return found


def extract_accessions_from_text(text: str) -> list[str]:
    """Return sequence/proteomics accessions mentioned in *text* (deduplicated)."""
    seen: list[str] = []
    for match in _ACCESSION_PATTERN.finditer(text):
        accession = match.group(1)
        if accession not in seen:
            seen.append(accession)
    return seen


# ---------------------------------------------------------------------------
# Orchestrator: route by DOI type; aggregate hosted + linked datasets
# ---------------------------------------------------------------------------

# Data-repository sources and their standalone retrievers.
_DATA_REPO_RETRIEVERS: dict[str, Callable[..., SupplementRetrievalResult]] = {
    "dryad": retrieve_supplements_from_dryad,
    "zenodo": retrieve_supplements_from_zenodo,
    "figshare": retrieve_supplements_from_figshare,
}


def _default_supplement_sources(doi: str) -> list[str]:
    """Pick the source order for *doi* (data-repo DOIs vs. article DOIs)."""
    repo = _repo_for_doi(doi)
    if repo:
        return [repo]
    return ["europepmc", "pmc_oa"]


def retrieve_supplements(
    doi: str,
    *,
    sources: list[str] | None = None,
    text: str | None = None,
    follow_related: bool = True,
    useful_kinds: Iterable[SupplementKind] = DEFAULT_USEFUL_KINDS,
    max_files: int = SUPPLEMENT_MAX_FILES,
    max_file_bytes: int = SUPPLEMENT_MAX_FILE_BYTES,
    max_total_bytes: int = SUPPLEMENT_MAX_TOTAL_BYTES,
    max_archive_bytes: int = SUPPLEMENT_MAX_ARCHIVE_BYTES,
    max_text_chars: int = SUPPLEMENT_MAX_TEXT_CHARS,
    save_dir: str | None = None,
) -> SupplementRetrievalResult:
    """Retrieve high-value supplements for *doi*, resolving in layers.

    Routing:

    * **Data-repository DOI** (Dryad/Zenodo/Figshare) -> fetch that repo's files.
    * **Publication DOI** -> hosted supplements (Europe PMC, then NCBI PMC OA as a
      fallback), plus, when ``follow_related``, any data-repository datasets linked
      from the article's Crossref/DataCite relation metadata, plus, when ``text``
      is given, data-repository DOIs mined from that text. Sequence/proteomics
      accessions found in ``text`` are surfaced in ``detected_accessions`` but not
      retrieved.

    Files from every contributing source are merged under a *shared* budget:
    hosted supplements take priority, and each linked dataset only fetches what
    remains of ``max_files`` / ``max_total_bytes``, so the total downloaded never
    exceeds the global caps. Each ``SupplementFile.source`` records its origin.
    Pass ``sources`` to run an explicit source list instead of the default routing.

    Returns:
        A merged :class:`SupplementRetrievalResult`. When nothing is kept, the
        result of the first source that at least found candidates is returned so
        its ``skipped``/``error`` detail is preserved.
    """
    doi = normalize_doi(doi)
    kwargs = {
        "useful_kinds": useful_kinds,
        "max_files": max_files,
        "max_file_bytes": max_file_bytes,
        "max_total_bytes": max_total_bytes,
        "max_archive_bytes": max_archive_bytes,
        "max_text_chars": max_text_chars,
        "save_dir": save_dir,
    }

    if sources is not None:
        # Thread the PMCID along the source list: whichever source resolves it
        # first (usually ``europepmc``) saves the later ones a repeat lookup.
        explicit: list[SupplementRetrievalResult] = []
        resolved_pmcid: str | None = None
        for source in sources:
            result = _run_source(source, doi, kwargs, resolved_pmcid)
            if result is None:
                continue
            resolved_pmcid = resolved_pmcid or result.pmcid
            explicit.append(result)
        return _merge_results(
            doi,
            explicit,
            [],
            max_files,
            max_total_bytes,
            save_dir=save_dir,
        )

    # Direct data-repository DOI: just that repo.
    repo = _repo_for_doi(doi)
    if repo:
        return _DATA_REPO_RETRIEVERS[repo](doi, **kwargs)  # type: ignore[arg-type]

    # Publication DOI: hosted supplements (stop at first with files), then any
    # linked/mined data-repository datasets.
    results: list[SupplementRetrievalResult] = []
    # The Europe PMC retriever already records the PMCID from its own lookup, so
    # the OA fallback reuses it rather than repeating the search request.
    hosted = retrieve_supplements_from_europepmc(doi, **kwargs)  # type: ignore[arg-type]
    pmcid: str | None = hosted.pmcid
    results.append(hosted)
    if not hosted.has_supplements and pmcid:
        results.append(retrieve_supplements_from_pmc_oa(pmcid, doi=doi, **kwargs))  # type: ignore[arg-type]

    data_dois: list[str] = []
    if follow_related:
        data_dois.extend(find_related_data_dois(doi))
    accessions: list[str] = []
    if text:
        for mined in extract_dataset_dois_from_text(text):
            if mined not in data_dois:
                data_dois.append(mined)
        accessions = extract_accessions_from_text(text)

    # Fetch linked datasets against a *shared* budget so the union across all
    # sources never downloads more than the global caps. Hosted supplements take
    # priority; each dataset gets only what remains.
    for data_doi in data_dois:
        repo = _repo_for_doi(data_doi)
        if repo is None:
            continue
        used_files = sum(len(r.files) for r in results)
        used_bytes = sum(f.size_bytes or 0 for r in results for f in r.files)
        if used_files >= max_files or used_bytes >= max_total_bytes:
            break  # global budget exhausted; stop fetching further datasets
        results.append(
            _DATA_REPO_RETRIEVERS[repo](
                data_doi,
                **{
                    **kwargs,
                    "max_files": max_files - used_files,
                    "max_total_bytes": max_total_bytes - used_bytes,
                },
            )  # type: ignore[arg-type]
        )

    merged = _merge_results(doi, results, accessions, max_files, max_total_bytes, save_dir=save_dir)
    merged.pmcid = pmcid or merged.pmcid
    return merged


def _run_source(
    source: str,
    doi: str,
    kwargs: dict[str, Any],
    pmcid: str | None,
) -> SupplementRetrievalResult | None:
    """Run a single named source (used for explicit ``sources`` overrides).

    *pmcid*, when already resolved by an earlier source, spares ``pmc_oa`` the
    Europe PMC search request it would otherwise issue to find one.
    """
    if source == "europepmc":
        return retrieve_supplements_from_europepmc(doi, **kwargs)
    if source == "pmc_oa":
        resolved = pmcid or (str(find_supplement_source_europepmc(doi).get("pmcid") or "") or None)
        if not resolved:
            return None
        return retrieve_supplements_from_pmc_oa(resolved, doi=doi, **kwargs)
    retriever = _DATA_REPO_RETRIEVERS.get(source)
    if retriever:
        return retriever(doi, **kwargs)
    return None


def _is_removable_temp_file(path: str) -> bool:
    """Return True when *path* is one of our own ``mkstemp`` files.

    Guards the merge-time cleanup: only files this module created under the
    system temp directory may be deleted.
    """
    try:
        temp_root = os.path.realpath(tempfile.gettempdir())
        return os.path.commonpath([temp_root, os.path.realpath(path)]) == temp_root
    except (OSError, ValueError):  # different drives, unresolvable path
        return False


def _merge_results(
    doi: str,
    results: list[SupplementRetrievalResult],
    accessions: list[str],
    max_files: int,
    max_total_bytes: int,
    save_dir: str | None = None,
) -> SupplementRetrievalResult:
    """Merge per-source results into one, trimming the union to global caps.

    Files dropped by the global caps have their temp file removed so nothing
    leaks. When *save_dir* is set the sources wrote into a caller-managed
    directory instead, so dropped paths are left alone -- they are the caller's
    outputs, not our scratch files.
    """
    attempts: list[str] = []
    for res in results:
        attempts.extend(res.attempts)

    kept: list[SupplementFile] = []
    seen: set[tuple[str | None, str]] = set()
    total_bytes = 0
    for res in results:
        for file in res.files:
            key = (file.source, os.path.basename(file.filename))
            keep = (
                key not in seen
                and len(kept) < max_files
                and total_bytes + (file.size_bytes or 0) <= max_total_bytes
            )
            if keep:
                seen.add(key)
                total_bytes += file.size_bytes or 0
                kept.append(file)
            elif file.saved_path and save_dir is None and _is_removable_temp_file(file.saved_path):
                # This file was already materialized to a temp file by its source
                # but is being dropped from the merged result (cap/dedup). Delete
                # it so it doesn't leak -- the caller can only clean files it sees.
                remove_temp_file(file.saved_path)

    merged = SupplementRetrievalResult(
        doi=doi,
        attempts=list(dict.fromkeys(attempts)),
        detected_accessions=accessions,
    )
    for res in results:
        if res.pmcid:
            merged.pmcid = res.pmcid
            break

    if kept:
        merged.files = kept
        merged.skipped = [s for res in results for s in res.skipped]
        merged.source = "+".join(dict.fromkeys(f.source for f in kept if f.source)) or None
        return merged

    # Nothing kept: surface the first source that at least found candidates.
    informative = next((res for res in results if res.skipped), None) or (
        results[0] if results else None
    )
    if informative is None:
        merged.error = "No supplement sources available for DOI"
        return merged
    merged.source = informative.source
    merged.skipped = informative.skipped
    merged.error = informative.error or "No high-value supplements found"
    return merged
