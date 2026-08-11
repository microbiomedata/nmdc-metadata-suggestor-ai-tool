"""Europe PMC ``supplementaryFiles``: a ZIP of an open-access article's supplements.

Captions come from the article's JATS full text, so supplements can be selected by
content rather than extension alone.
"""

import io
import zipfile
from collections.abc import Iterable

from nmdc_metadata_suggestor_ai_tool.constants import (
    DEFAULT_TIMEOUT,
    EUROPEPMC_API_URL,
    EUROPEPMC_FULLTEXT_XML_URL_TEMPLATE,
    EUROPEPMC_SUPPL_URL_TEMPLATE,
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
    zip_reader,
)


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
    pmcid = article.get("pmcid")
    result["pmcid"] = pmcid
    result["is_open_access"] = article.get("isOpenAccess") == "Y"
    # ``hasSuppl`` is "Y"/"N" when present. Treat only an explicit "Y" as a
    # signal to attempt download, so we avoid wasted requests (performance-first).
    result["has_supplements"] = article.get("hasSuppl") == "Y"
    # The supplement endpoint keys on the PMCID alone. A record without one (not
    # in PMC) has no supplements to serve, whatever ``source``/``id`` it carries.
    if pmcid:
        result["zip_url"] = EUROPEPMC_SUPPL_URL_TEMPLATE.format(pmcid=pmcid)
    return result


def fetch_europepmc_captions(pmcid: str) -> dict[str, str]:
    """Best-effort fetch of supplement captions from Europe PMC full-text JATS.

    The JATS document is size-bounded like every other download: an over-large
    (or missing/non-200) document yields no captions rather than being buffered.
    """
    url = EUROPEPMC_FULLTEXT_XML_URL_TEMPLATE.format(pmcid=pmcid)
    try:
        data = download_bounded(url, SUPPLEMENT_MAX_XML_BYTES)
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
        archive_bytes = download_bounded(str(located["zip_url"]), caps.max_archive_bytes)
    except RuntimeError as exc:
        result.error = str(exc)
        return result

    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile:
        result.error = "Supplement archive was not a valid ZIP"
        return result

    captions: dict[str, str] = {}
    if include_captions and located.get("pmcid"):
        captions = fetch_europepmc_captions(str(located["pmcid"]))

    with archive:
        members = [
            Member(name=info.filename, size=info.file_size, read=zip_reader(archive, info))
            for info in archive.infolist()
        ]
        apply_selection(
            result, members, caps, captions, "Archive contained no high-value supplement files"
        )
    return result
