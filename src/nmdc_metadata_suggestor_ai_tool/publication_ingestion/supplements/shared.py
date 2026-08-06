"""Pieces every supplement source shares: bounded downloads, member selection, captions.

A *member* is one candidate supplement file from any source -- a ZIP entry or a
per-file download URL. Sources turn their listings into
:class:`Member` objects and hand them to :func:`select_members`, which applies the
kind filter and the caps uniformly, so "which files do we keep, and how big may
they be" is decided in one place rather than five.
"""

import io
import logging
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_TIMEOUT,
    UNSAFE_XML_DECLARATION_PATTERN,
    USER_AGENT,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    normalize_doi,
    request_with_retry,
)
from nmdc_metadata_suggestor_ai_tool.file_kinds import (
    TEXT_LIKE_EXTENSIONS,
    classify_file,
    file_extension,
)
from nmdc_metadata_suggestor_ai_tool.models.supplement import (
    SupplementFile,
    SupplementKind,
    SupplementRetrievalResult,
)

logger = logging.getLogger(__name__)

# Backwards-compatible alias: the file-type taxonomy now lives in ``file_kinds``.
# ``classify_supplement`` remains the documented name used by the skill/tests.
classify_supplement = classify_file


def download_bounded(url: str, max_bytes: int) -> bytes:
    """Download *url* into memory, aborting if it exceeds *max_bytes*.

    Used for both the Europe PMC ZIP and single-file downloads (Zenodo/Figshare),
    so the raised messages stay source-agnostic.
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


def is_unsafe_member(name: str) -> bool:
    """Return True for archive member names that could escape the extraction dir."""
    if not name or name.endswith("/"):
        return True  # directory entry
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or ".." in normalized.split("/"):
        return True
    return False


def materialize_member(
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


def localname(tag: str) -> str:
    """Return the XML localname, dropping any ``{namespace}`` prefix."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def caption_key(filename: str) -> str:
    """Normalize a filename/href to a match key (basename, no extension, lower)."""
    base = os.path.basename(filename).lower()
    stem, _, ext = base.rpartition(".")
    return stem if (ext and stem) else base


def parse_supplement_captions(xml_text: str | bytes) -> dict[str, str]:
    """Map supplement filenames to their captions from JATS full-text XML.

    Reads ``<supplementary-material>``/``<media>`` elements, keying each caption
    on the referenced file's normalized basename (see :func:`caption_key`).

    Args:
        xml_text: JATS XML document (Europe PMC ``fullTextXML``).

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
        if localname(elem.tag) not in supplement_tags:
            continue
        href = next(
            (v for k, v in elem.attrib.items() if localname(k) == "href"),
            None,
        )
        if not href:
            continue
        text = re.sub(r"\s+", " ", " ".join(elem.itertext())).strip()
        key = caption_key(href)
        if key and text and key not in captions:
            captions[key] = text[:1000]
    return captions


def match_caption(filename: str, captions: dict[str, str] | None) -> str | None:
    """Look up a caption for *filename* by its normalized key."""
    if not captions:
        return None
    return captions.get(caption_key(filename))


# ---------------------------------------------------------------------------
# Shared member selection: kind filter + caps + materialization + captions
# ---------------------------------------------------------------------------


class Member(NamedTuple):
    """A candidate supplement file from any source."""

    name: str
    size: int  # reported (uncompressed) size; used for pre-read cap checks
    read: Callable[[], bytes]


def zip_reader(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> Callable[[], bytes]:
    """Return a no-arg reader for a ZIP member (bound so it survives the loop)."""
    return lambda: archive.read(info)


def url_reader(url: str, max_bytes: int) -> Callable[[], bytes]:
    """Return a no-arg bounded downloader for a per-file URL (e.g. Zenodo)."""
    return lambda: download_bounded(url, max_bytes)


def select_members(
    members: Iterable[Member],
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
                caption=match_caption(name, captions),
                skipped_reason=reason,
            )
        )

    for member in members:
        name = member.name
        if is_unsafe_member(name):
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

        text, saved_path = materialize_member(kind, name, data, max_text_chars, save_dir)
        total_kept_bytes += actual
        kept.append(
            SupplementFile(
                filename=name,
                kind=kind,
                source=source,
                size_bytes=actual,
                caption=match_caption(name, captions),
                text=text,
                saved_path=saved_path,
            )
        )

    return kept, skipped


class SupplementCaps(NamedTuple):
    """Bundle of the retrieval caps shared by every source."""

    useful_kinds: frozenset[SupplementKind]
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    max_archive_bytes: int
    max_text_chars: int
    save_dir: str | None


def apply_selection(
    result: SupplementRetrievalResult,
    members: Iterable[Member],
    caps: SupplementCaps,
    captions: dict[str, str] | None,
    empty_error: str,
) -> SupplementRetrievalResult:
    """Run the shared selector over *members* and populate *result*."""
    kept, skipped = select_members(
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


def first_matching(
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
