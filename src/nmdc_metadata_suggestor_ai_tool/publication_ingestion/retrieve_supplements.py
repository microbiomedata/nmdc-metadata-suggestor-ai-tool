"""Retrieve supplementary materials for a manuscript DOI.

The goal is to give the NMDC metadata suggestor extra context for characterizing
submissions and samples -- not to comprehensively mirror every supplement. This
module therefore favors *performance over completeness*:

* A single programmatic source is used: the Europe PMC ``supplementaryFiles``
  endpoint, which returns a ZIP of an open-access article's supplements.
* Only high-value file kinds (tabular data and documents; see
  :class:`~nmdc_metadata_suggestor_ai_tool.models.supplement.SupplementKind`)
  are kept by default.
* Downloads are bounded by size/count caps and are never retried beyond
  ``request_with_retry``'s transient-error handling. If supplements are hard to
  obtain, we give up quickly rather than hammering the source.

For publisher-hosted supplements behind Cloudflare/JS challenges, prefer the
agentic ``web_fetch`` path documented in the ``supplement-retrieval`` skill.
"""

import io
import logging
import os
import tempfile
import zipfile
from collections.abc import Iterable

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_TIMEOUT,
    EUROPEPMC_API_URL,
    EUROPEPMC_SUPPL_URL_TEMPLATE,
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
    """Return True for ZIP member names that could escape the extraction dir."""
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
) -> SupplementRetrievalResult:
    """Retrieve high-value supplements for *doi* via Europe PMC.

    Text-like supplements (csv/tsv/txt) are inlined into ``SupplementFile.text``;
    other kept supplements are written to temp files (or ``save_dir``) and
    exposed via ``SupplementFile.saved_path``. Low-value kinds and files
    exceeding the caps are recorded in ``result.skipped`` rather than kept.

    Args:
        doi: Digital Object Identifier in any common format.
        useful_kinds: Supplement kinds to keep. Defaults to tabular + document.
        max_files: Max number of supplement files to keep.
        max_file_bytes: Skip any single (uncompressed) file larger than this.
        max_total_bytes: Stop keeping files once this combined size is reached.
        max_archive_bytes: Skip the whole archive if it exceeds this download cap.
        max_text_chars: Truncate inlined text to this many characters.
        save_dir: Directory for non-text files; a temp dir is used when omitted.

    Returns:
        A :class:`SupplementRetrievalResult`. ``error`` is set (and ``files``
        empty) when no supplements could be retrieved.
    """
    kept_kinds = frozenset(useful_kinds)
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

    zip_url = str(located["zip_url"])
    try:
        archive_bytes = _download_bounded(zip_url, max_archive_bytes)
    except RuntimeError as exc:
        result.error = str(exc)
        return result

    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile:
        result.error = "Supplement archive was not a valid ZIP"
        return result

    total_kept_bytes = 0
    with archive:
        for info in archive.infolist():
            name = info.filename
            if _is_unsafe_member(name):
                continue
            kind = classify_supplement(name)
            size = info.file_size

            if kind not in kept_kinds:
                result.skipped.append(
                    SupplementFile(
                        filename=name,
                        kind=kind,
                        size_bytes=size,
                        skipped_reason=f"low-value kind ({kind.value})",
                    )
                )
                continue
            if size > max_file_bytes:
                result.skipped.append(
                    SupplementFile(
                        filename=name,
                        kind=kind,
                        size_bytes=size,
                        skipped_reason=f"file exceeds {max_file_bytes} byte cap",
                    )
                )
                continue
            if len(result.files) >= max_files:
                result.skipped.append(
                    SupplementFile(
                        filename=name,
                        kind=kind,
                        size_bytes=size,
                        skipped_reason=f"max_files ({max_files}) reached",
                    )
                )
                continue
            if total_kept_bytes + size > max_total_bytes:
                result.skipped.append(
                    SupplementFile(
                        filename=name,
                        kind=kind,
                        size_bytes=size,
                        skipped_reason=f"max_total_bytes ({max_total_bytes}) reached",
                    )
                )
                continue

            try:
                data = archive.read(info)
            except Exception as exc:
                logger.debug("Failed to read supplement member %s: %s", name, exc)
                result.skipped.append(
                    SupplementFile(
                        filename=name,
                        kind=kind,
                        size_bytes=size,
                        skipped_reason="read error",
                    )
                )
                continue

            text, saved_path = _materialize_member(kind, name, data, max_text_chars, save_dir)
            total_kept_bytes += size
            result.files.append(
                SupplementFile(
                    filename=name,
                    kind=kind,
                    size_bytes=size,
                    text=text,
                    saved_path=saved_path,
                )
            )

    if not result.files:
        result.error = "Archive contained no high-value supplement files"
    return result


# Public alias: the single generic entry point. Europe PMC is currently the only
# programmatic source; keeping a neutral name lets callers stay source-agnostic.
retrieve_supplements = retrieve_supplements_from_europepmc
