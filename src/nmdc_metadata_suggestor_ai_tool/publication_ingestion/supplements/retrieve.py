"""The orchestrator: route by DOI type, aggregate hosted + linked datasets, merge.

Every contributing source draws on one *shared* budget, so the union of files
returned never exceeds the global caps no matter how many repositories a
publication points at.
"""

import os
import tempfile
from collections.abc import Callable, Iterable
from typing import Any

from nmdc_metadata_suggestor_ai_tool.constants import (
    SUPPLEMENT_MAX_ARCHIVE_BYTES,
    SUPPLEMENT_MAX_FILE_BYTES,
    SUPPLEMENT_MAX_FILES,
    SUPPLEMENT_MAX_TEXT_CHARS,
    SUPPLEMENT_MAX_TOTAL_BYTES,
)
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import normalize_doi
from nmdc_metadata_suggestor_ai_tool.file_kinds import DEFAULT_USEFUL_KINDS
from nmdc_metadata_suggestor_ai_tool.models.supplement import (
    SupplementFile,
    SupplementKind,
    SupplementRetrievalResult,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import remove_temp_file
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.dryad import (
    retrieve_supplements_from_dryad,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.europepmc import (
    retrieve_supplements_from_europepmc,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.figshare import (
    retrieve_supplements_from_figshare,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.related_dois import (
    extract_accessions_from_text,
    extract_dataset_dois_from_text,
    find_related_data_dois,
    repo_for_doi,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.zenodo import (
    retrieve_supplements_from_zenodo,
)

# Data-repository sources and their standalone retrievers.
DATA_REPO_RETRIEVERS: dict[str, Callable[..., SupplementRetrievalResult]] = {
    "dryad": retrieve_supplements_from_dryad,
    "zenodo": retrieve_supplements_from_zenodo,
    "figshare": retrieve_supplements_from_figshare,
}


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
    * **Publication DOI** -> hosted supplements (Europe PMC), plus, when
      ``follow_related``, any data-repository datasets linked from the article's
      Crossref/DataCite relation metadata, plus, when ``text`` is given,
      data-repository DOIs mined from that text. Sequence/proteomics accessions
      found in ``text`` are surfaced in ``detected_accessions`` but not retrieved.

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
        explicit = [run_source(source, doi, kwargs) for source in sources]
        return merge_results(
            doi,
            [result for result in explicit if result is not None],
            [],
            max_files,
            max_total_bytes,
            save_dir=save_dir,
        )

    # Direct data-repository DOI: just that repo.
    repo = repo_for_doi(doi)
    if repo:
        return DATA_REPO_RETRIEVERS[repo](doi, **kwargs)  # type: ignore[arg-type]

    # Publication DOI: hosted supplements, then any linked/mined datasets.
    hosted = retrieve_supplements_from_europepmc(doi, **kwargs)  # type: ignore[arg-type]
    pmcid: str | None = hosted.pmcid
    results: list[SupplementRetrievalResult] = [hosted]

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
        repo = repo_for_doi(data_doi)
        if repo is None:
            continue
        used_files = sum(len(r.files) for r in results)
        used_bytes = sum(f.size_bytes or 0 for r in results for f in r.files)
        if used_files >= max_files or used_bytes >= max_total_bytes:
            break  # global budget exhausted; stop fetching further datasets
        results.append(
            DATA_REPO_RETRIEVERS[repo](
                data_doi,
                **{
                    **kwargs,
                    "max_files": max_files - used_files,
                    "max_total_bytes": max_total_bytes - used_bytes,
                },
            )  # type: ignore[arg-type]
        )

    merged = merge_results(doi, results, accessions, max_files, max_total_bytes, save_dir=save_dir)
    merged.pmcid = pmcid or merged.pmcid
    return merged


def run_source(
    source: str,
    doi: str,
    kwargs: dict[str, Any],
) -> SupplementRetrievalResult | None:
    """Run a single named source (used for explicit ``sources`` overrides)."""
    if source == "europepmc":
        return retrieve_supplements_from_europepmc(doi, **kwargs)
    retriever = DATA_REPO_RETRIEVERS.get(source)
    if retriever:
        return retriever(doi, **kwargs)
    return None


def is_removable_temp_file(path: str) -> bool:
    """Return True when *path* is one of our own ``mkstemp`` files.

    Guards the merge-time cleanup: only files this package created under the
    system temp directory may be deleted.
    """
    try:
        temp_root = os.path.realpath(tempfile.gettempdir())
        return os.path.commonpath([temp_root, os.path.realpath(path)]) == temp_root
    except (OSError, ValueError):  # different drives, unresolvable path
        return False


def merge_results(
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
            # Key on the full filename, not the basename: one source can legitimately
            # carry two distinct files that share a basename (e.g. ``a/Table_S1.xlsx``
            # and ``b/Table_S1.xlsx``), and collapsing them would lose one.
            key = (file.source, file.filename)
            keep = (
                key not in seen
                and len(kept) < max_files
                and total_bytes + (file.size_bytes or 0) <= max_total_bytes
            )
            if keep:
                seen.add(key)
                total_bytes += file.size_bytes or 0
                kept.append(file)
            elif file.saved_path and save_dir is None and is_removable_temp_file(file.saved_path):
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
