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
    DEFAULT_TIMEOUT,
    DRYAD_API_URL,
    DRYAD_DOI_PREFIX,
    EUROPEPMC_API_URL,
    EUROPEPMC_FULLTEXT_XML_URL_TEMPLATE,
    EUROPEPMC_SUPPL_URL_TEMPLATE,
    PMC_OA_SERVICE_URL,
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
from nmdc_metadata_suggestor_ai_tool.models.supplement import (
    SupplementFile,
    SupplementKind,
    SupplementRetrievalResult,
)

logger = logging.getLogger(__name__)

# Extension -> kind mapping. Extensions not listed classify as ``OTHER``.
_EXTENSION_KINDS: dict[str, SupplementKind] = {
    # Tabular: highest value -- typically per-sample metadata/measurements.
    "csv": SupplementKind.TABULAR,
    "tsv": SupplementKind.TABULAR,
    "tab": SupplementKind.TABULAR,
    "xlsx": SupplementKind.TABULAR,
    "xls": SupplementKind.TABULAR,
    "xlsm": SupplementKind.TABULAR,
    "ods": SupplementKind.TABULAR,
    # Documents: supplementary methods, site descriptions, etc.
    "pdf": SupplementKind.DOCUMENT,
    "docx": SupplementKind.DOCUMENT,
    "doc": SupplementKind.DOCUMENT,
    "rtf": SupplementKind.DOCUMENT,
    "txt": SupplementKind.DOCUMENT,
    "text": SupplementKind.DOCUMENT,
    "md": SupplementKind.DOCUMENT,
    # Images / media -- rarely useful for value-filling.
    "png": SupplementKind.IMAGE,
    "jpg": SupplementKind.IMAGE,
    "jpeg": SupplementKind.IMAGE,
    "gif": SupplementKind.IMAGE,
    "tif": SupplementKind.IMAGE,
    "tiff": SupplementKind.IMAGE,
    "bmp": SupplementKind.IMAGE,
    "eps": SupplementKind.IMAGE,
    "svg": SupplementKind.IMAGE,
    "mov": SupplementKind.MEDIA,
    "mp4": SupplementKind.MEDIA,
    "avi": SupplementKind.MEDIA,
    "wmv": SupplementKind.MEDIA,
    "mp3": SupplementKind.MEDIA,
    "wav": SupplementKind.MEDIA,
    # Sequence / large omics data -- out of scope for metadata suggestion.
    "fasta": SupplementKind.SEQUENCE,
    "fa": SupplementKind.SEQUENCE,
    "fastq": SupplementKind.SEQUENCE,
    "fq": SupplementKind.SEQUENCE,
    "sam": SupplementKind.SEQUENCE,
    "bam": SupplementKind.SEQUENCE,
    "vcf": SupplementKind.SEQUENCE,
    "gb": SupplementKind.SEQUENCE,
    "gbk": SupplementKind.SEQUENCE,
    # Nested archives.
    "zip": SupplementKind.ARCHIVE,
    "gz": SupplementKind.ARCHIVE,
    "tar": SupplementKind.ARCHIVE,
    "tgz": SupplementKind.ARCHIVE,
    "7z": SupplementKind.ARCHIVE,
    "rar": SupplementKind.ARCHIVE,
}

# Kinds whose content is decodable as plain text and worth inlining directly.
_TEXT_KINDS_EXTENSIONS = {"csv", "tsv", "tab", "txt", "text", "md"}

# High-value kinds kept by default.
DEFAULT_USEFUL_KINDS: frozenset[SupplementKind] = frozenset(
    {SupplementKind.TABULAR, SupplementKind.DOCUMENT}
)


def _extension(filename: str) -> str:
    """Return the lowercase extension of *filename* without the dot."""
    base = os.path.basename(filename)
    _, _, ext = base.rpartition(".")
    return ext.lower() if "." in base else ""


def classify_supplement(filename: str) -> SupplementKind:
    """Classify a supplement file by its extension.

    Args:
        filename: The supplement's filename (or path within an archive).

    Returns:
        The :class:`SupplementKind` for the file, or ``OTHER`` if unrecognized.
    """
    return _EXTENSION_KINDS.get(_extension(filename), SupplementKind.OTHER)


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
            raise RuntimeError(f"Archive size {declared} bytes exceeds cap {max_bytes} bytes")
        buffer = io.BytesIO()
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError(f"Archive exceeded size cap of {max_bytes} bytes")
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
    if _extension(filename) in _TEXT_KINDS_EXTENSIONS:
        text = data.decode("utf-8", errors="replace")
        if len(text) > max_text_chars:
            text = text[:max_text_chars] + "\n...[truncated]"
        return text, None

    suffix = f".{_extension(filename)}" if _extension(filename) else ""
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
        root = ET.fromstring(xml_text if isinstance(xml_text, str) else xml_text.decode("utf-8"))
    except (ET.ParseError, UnicodeDecodeError):
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
) -> tuple[list[SupplementFile], list[SupplementFile]]:
    """Filter candidate members to the high-value ones within caps.

    Returns ``(kept, skipped)``. Caps are checked against each member's reported
    size *before* reading, so oversized files are never downloaded/extracted.
    """
    kept: list[SupplementFile] = []
    skipped: list[SupplementFile] = []
    total_kept_bytes = 0

    def skip(name: str, kind: SupplementKind, size: int, reason: str) -> None:
        skipped.append(
            SupplementFile(
                filename=name,
                kind=kind,
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

        text, saved_path = _materialize_member(kind, name, data, max_text_chars, save_dir)
        total_kept_bytes += size or len(data)
        kept.append(
            SupplementFile(
                filename=name,
                kind=kind,
                size_bytes=size or len(data),
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
    """Best-effort fetch of supplement captions from Europe PMC full-text JATS."""
    url = EUROPEPMC_FULLTEXT_XML_URL_TEMPLATE.format(source=source, article_id=article_id)
    try:
        response = request_with_retry(
            "GET", url, timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        if response.status_code != 200:
            return {}
        return parse_supplement_captions(response.content)
    except Exception:
        return {}


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


def _read_tar_member(tar: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    """Read a regular-file member's bytes from an open tar archive."""
    handle = tar.extractfile(member)
    if handle is None:
        raise RuntimeError("member is not a regular file")
    with handle:
        return handle.read()


def _captions_from_tar(tar: tarfile.TarFile) -> dict[str, str]:
    """Parse supplement captions from the first ``.nxml`` in an OA package."""
    for member in tar.getmembers():
        if member.isfile() and member.name.lower().endswith(".nxml"):
            try:
                return parse_supplement_captions(_read_tar_member(tar, member))
            except Exception:
                return {}
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
    """Retrieve high-value files from a Dryad dataset DOI.

    Dryad files are individual downloads (not an archive), so each kept file is
    fetched on demand with a bounded per-file download. File descriptions, when
    present, are attached as captions.
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

    captions: dict[str, str] = {}
    members: list[_Member] = []
    for item in items:
        path = item.get("path")
        download_href = item.get("_links", {}).get("stash:download", {}).get("href")
        if not path or not download_href:
            continue
        description = item.get("description")
        if description:
            captions[_caption_key(path)] = str(description)[:1000]
        members.append(
            _Member(
                name=path,
                size=int(item.get("size") or 0),
                read=_url_reader(_dryad_url(download_href), caps.max_file_bytes),
            )
        )

    _apply_selection(result, members, caps, captions, "Dryad dataset contained no high-value files")
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
# Orchestrator: try sources in order, stop at the first that yields files
# ---------------------------------------------------------------------------


def _default_supplement_sources(doi: str) -> list[str]:
    """Pick the source order for *doi* (Dryad DOIs vs. article DOIs)."""
    if is_dryad_doi(doi):
        return ["dryad"]
    return ["europepmc", "pmc_oa"]


def retrieve_supplements(
    doi: str,
    *,
    sources: list[str] | None = None,
    useful_kinds: Iterable[SupplementKind] = DEFAULT_USEFUL_KINDS,
    max_files: int = SUPPLEMENT_MAX_FILES,
    max_file_bytes: int = SUPPLEMENT_MAX_FILE_BYTES,
    max_total_bytes: int = SUPPLEMENT_MAX_TOTAL_BYTES,
    max_archive_bytes: int = SUPPLEMENT_MAX_ARCHIVE_BYTES,
    max_text_chars: int = SUPPLEMENT_MAX_TEXT_CHARS,
    save_dir: str | None = None,
) -> SupplementRetrievalResult:
    """Retrieve high-value supplements for *doi*, trying sources in order.

    The default order is Europe PMC then the NCBI PMC OA package (fallback), or
    just Dryad for Dryad dataset DOIs. Retrieval stops at the first source that
    returns any kept file. Pass ``sources`` to override (``"europepmc"``,
    ``"pmc_oa"``, ``"dryad"``).

    Returns:
        The first :class:`SupplementRetrievalResult` with kept files, or the last
        attempted result (with its error) when no source yields supplements.
    """
    doi = normalize_doi(doi)
    order = sources or _default_supplement_sources(doi)
    kwargs = {
        "useful_kinds": useful_kinds,
        "max_files": max_files,
        "max_file_bytes": max_file_bytes,
        "max_total_bytes": max_total_bytes,
        "max_archive_bytes": max_archive_bytes,
        "max_text_chars": max_text_chars,
        "save_dir": save_dir,
    }

    attempts: list[str] = []
    pmcid: str | None = None
    # When no source yields kept files, prefer the first source that at least
    # found candidates (non-empty ``skipped``) so its detail isn't lost.
    informative: SupplementRetrievalResult | None = None
    last: SupplementRetrievalResult | None = None

    for source in order:
        if source == "europepmc":
            res = retrieve_supplements_from_europepmc(doi, **kwargs)  # type: ignore[arg-type]
            pmcid = res.pmcid or pmcid
        elif source == "pmc_oa":
            if not pmcid:
                pmcid = str(find_supplement_source_europepmc(doi).get("pmcid") or "") or None
            if not pmcid:
                attempts.append("pmc_oa")
                continue
            res = retrieve_supplements_from_pmc_oa(pmcid, doi=doi, **kwargs)  # type: ignore[arg-type]
        elif source == "dryad":
            res = retrieve_supplements_from_dryad(doi, **kwargs)  # type: ignore[arg-type]
        else:
            continue

        attempts.extend(res.attempts)
        last = res
        if res.has_supplements:
            res.attempts = list(dict.fromkeys(attempts))
            return res
        if informative is None and res.skipped:
            informative = res

    chosen = informative or last
    if chosen is None:
        return SupplementRetrievalResult(
            doi=doi, attempts=attempts, error="No supplement sources available for DOI"
        )
    chosen.attempts = list(dict.fromkeys(attempts))
    return chosen
