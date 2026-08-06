"""NCBI PMC Open Access Web Service: the article's ``.tar.gz`` package.

Used as a fallback sibling to Europe PMC. Captions come from the ``.nxml``
bundled inside the package, so selecting supplements by content costs no extra
request.
"""

import io
import logging
import re
import tarfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_TIMEOUT,
    PMC_OA_SERVICE_URL,
    SUPPLEMENT_MAX_ARCHIVE_BYTES,
    SUPPLEMENT_MAX_FILE_BYTES,
    SUPPLEMENT_MAX_FILES,
    SUPPLEMENT_MAX_TEXT_CHARS,
    SUPPLEMENT_MAX_TOTAL_BYTES,
    SUPPLEMENT_MAX_XML_BYTES,
    USER_AGENT,
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
    download_bounded,
    parse_supplement_captions,
    read_tar_member,
    tar_reader,
)

logger = logging.getLogger(__name__)


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
    caps = SupplementCaps(
        useful_kinds=frozenset(useful_kinds),
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        max_archive_bytes=max_archive_bytes,
        max_text_chars=max_text_chars,
        save_dir=save_dir,
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
        archive_bytes = download_bounded(str(located["tgz_url"]), caps.max_archive_bytes)
    except RuntimeError as exc:
        result.error = str(exc)
        return result

    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            captions = captions_from_tar(tar)
            members = [
                Member(name=info.name, size=info.size, read=tar_reader(tar, info))
                for info in tar.getmembers()
                if info.isfile()
            ]
            apply_selection(
                result, members, caps, captions, "OA package contained no high-value supplements"
            )
    except (tarfile.TarError, OSError) as exc:
        result.error = f"Failed to read OA package: {exc}"
    return result


def captions_from_tar(tar: tarfile.TarFile) -> dict[str, str]:
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
            data = read_tar_member(tar, member, max_bytes=SUPPLEMENT_MAX_XML_BYTES)
        except Exception:
            continue
        return parse_supplement_captions(data)
    return {}
