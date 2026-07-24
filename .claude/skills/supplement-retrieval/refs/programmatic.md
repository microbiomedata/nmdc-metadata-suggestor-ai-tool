# Supplement Retrieval — Programmatic API

**Import path:** `nmdc_metadata_suggestor_ai_tool.publication_ingestion.retrieve_supplements`

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.retrieve_supplements import (
    retrieve_supplements,                 # orchestrator: routes + merges by DOI type
    retrieve_supplements_from_europepmc,  # Europe PMC supplementaryFiles ZIP
    retrieve_supplements_from_pmc_oa,     # NCBI PMC OA .tar.gz package (by PMCID)
    retrieve_supplements_from_dryad,      # Dryad dataset DOI files
    retrieve_supplements_from_zenodo,     # Zenodo record files (by DOI/concept DOI)
    retrieve_supplements_from_figshare,   # Figshare article/collection DOI files
    find_related_data_dois,               # publication DOI -> linked data-repo DOIs
    extract_dataset_dois_from_text,       # text -> Dryad/Zenodo/Figshare DOIs
    extract_accessions_from_text,         # text -> PRJNA…/SRR…/GSE… accessions
    find_supplement_source_europepmc,     # availability check, no download
    find_supplement_source_pmc_oa,        # resolve PMCID -> OA package URL
    parse_supplement_captions,            # JATS XML -> {filename_key: caption}
    classify_supplement,                  # filename -> SupplementKind
    is_dryad_doi,                         # True for 10.5061/… DOIs
    DEFAULT_USEFUL_KINDS,                 # {TABULAR, DOCUMENT}
)
```

## `retrieve_supplements(doi, ...)`

```python
retrieve_supplements(
    doi: str,
    *,
    sources: list[str] | None = None,  # explicit list of europepmc/pmc_oa/dryad/zenodo/figshare
    text: str | None = None,           # abstract/PDF text to mine for dataset DOIs + accessions
    follow_related: bool = True,       # follow Crossref/DataCite relation metadata
    useful_kinds: Iterable[SupplementKind] = DEFAULT_USEFUL_KINDS,
    max_files: int = SUPPLEMENT_MAX_FILES,            # default 10
    max_file_bytes: int = SUPPLEMENT_MAX_FILE_BYTES,  # default 10 MB
    max_total_bytes: int = SUPPLEMENT_MAX_TOTAL_BYTES,  # default 50 MB
    max_archive_bytes: int = SUPPLEMENT_MAX_ARCHIVE_BYTES,  # default 100 MB
    max_text_chars: int = SUPPLEMENT_MAX_TEXT_CHARS,  # default 200_000
    save_dir: str | None = None,
) -> SupplementRetrievalResult
```

**Routing & merge.** A data-repository DOI (`10.5061` Dryad, `10.5281` Zenodo,
`10.6084` Figshare) fetches that repo's files only. A publication DOI fetches
hosted supplements (Europe PMC, then NCBI PMC OA as a fallback) **plus** any
data-repository datasets found via relation metadata (`follow_related`) or mined
from `text`. Files from all contributing sources are merged and trimmed to
`max_files` / `max_total_bytes`; `SupplementFile.source` records each file's
origin and `result.source` joins the contributors with `+`. When nothing is kept,
the first source that *found* candidates is returned so its `skipped`/`error`
detail is preserved. Pass an explicit `sources=[...]` to bypass routing.

Each source also has a standalone function with the same caps (see imports above).
`retrieve_supplements_from_pmc_oa` takes a `pmcid` (plus optional `doi=`).

All caps are also configurable via `NMDC_SUPPLEMENT_*` environment variables
(see `constants.py`). Downloads are streamed and size-bounded; oversized files are
skipped by reported size *before* being read, and no archive is buffered beyond
`max_archive_bytes`. Across a publication's sources the caps act as one **shared
budget**: hosted supplements are fetched first and each linked dataset only pulls
what remains of `max_files` / `max_total_bytes`, so the total downloaded never
exceeds the global caps.

## Return: `SupplementRetrievalResult`

**Import path:** `nmdc_metadata_suggestor_ai_tool.models.supplement`

| Field | Type | Description |
|---|---|---|
| `doi` | `str` | Normalized DOI. |
| `source` | `str \| None` | Contributing source(s), joined by `+` (e.g. `"europepmc+zenodo"`). |
| `pmcid` | `str \| None` | PMC id when available. |
| `files` | `list[SupplementFile]` | Kept high-value supplements, merged across sources. |
| `skipped` | `list[SupplementFile]` | Found but dropped (with `skipped_reason`). |
| `attempts` | `list[str]` | Sources tried, in order. |
| `detected_accessions` | `list[str]` | Sequence/proteomics accessions mined from `text` (not retrieved). |
| `error` | `str \| None` | Why nothing was kept (e.g. no OA supplements). |
| `has_supplements` | `bool` (property) | True when `files` is non-empty. |

### `SupplementFile`

| Field | Type | Description |
|---|---|---|
| `filename` | `str` | Name within the archive / repository. |
| `kind` | `SupplementKind` | `tabular`, `document`, `image`, `media`, `sequence`, `archive`, `other`. |
| `source` | `str \| None` | Origin source: `europepmc`/`pmc_oa`/`dryad`/`zenodo`/`figshare`. |
| `size_bytes` | `int \| None` | Uncompressed size. |
| `caption` | `str \| None` | JATS/repository caption or description, when available. |
| `text` | `str \| None` | Inlined content for text-like files (csv/tsv/txt), truncated. |
| `saved_path` | `str \| None` | Temp-file path for non-text kept files (xlsx/pdf/docx). |
| `skipped_reason` | `str \| None` | Set only on entries in `result.skipped`. |

## Related datasets & text mining

```python
related = find_related_data_dois(publication_doi)   # -> ["10.5281/zenodo.X", ...]
dataset_dois = extract_dataset_dois_from_text(text)  # data-repo DOIs in prose
accessions = extract_accessions_from_text(text)      # PRJNA…/SRR…/GSE… (not fetched)
```

`find_related_data_dois` reads Crossref `relation` and DataCite
`relatedIdentifiers`, keeping only DOIs whose relation indicates associated data
(`is-supplemented-by`, `has-part`, `is-source-of`, `is-derived-from`,
`is-documented-by`) and that resolve to a fetchable repo (Dryad/Zenodo/Figshare).
`retrieve_supplements` calls both automatically for publication DOIs; use them
directly if you want to inspect or filter the links first.

## Temp-file cleanup

Non-text supplements are written to temp files. When done, remove them with the
shared helper:

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import remove_temp_files

remove_temp_files([f.saved_path for f in result.files if f.saved_path])
```

Passing `save_dir=...` writes into a directory you manage instead of temp files.

## Availability checks (no download)

```python
info = find_supplement_source_europepmc(doi)
# {"source", "article_id", "pmcid", "is_open_access", "has_supplements", "zip_url", "error"?}

oa = find_supplement_source_pmc_oa("PMC123456")
# {"pmcid", "tgz_url", "error"?}
```

Europe PMC only downloads when `has_supplements` is true (`hasSuppl == "Y"`),
avoiding wasted requests for articles with no open-access supplements. The OA
service returns an `error` (e.g. `idDoesNotExist`) when the article is not in the
OA subset.

## Captions (content-based selection)

`parse_supplement_captions(jats_xml)` returns `{caption_key: caption}` where the
key is the referenced file's basename without extension, lowercased. Europe PMC
pulls these from the article `fullTextXML`; PMC OA pulls them from the bundled
`.nxml`. Captions populate `SupplementFile.caption` so you can judge relevance
("Table S1. Sample metadata…") beyond the file extension.
