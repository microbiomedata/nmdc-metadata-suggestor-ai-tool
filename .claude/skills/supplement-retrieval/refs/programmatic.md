# Supplement Retrieval — Programmatic API

**Import path:** `nmdc_metadata_suggestor_ai_tool.publication_ingestion.retrieve_supplements`

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.retrieve_supplements import (
    retrieve_supplements,                 # orchestrator: europepmc -> pmc_oa (or dryad)
    retrieve_supplements_from_europepmc,  # Europe PMC supplementaryFiles ZIP
    retrieve_supplements_from_pmc_oa,     # NCBI PMC OA .tar.gz package (by PMCID)
    retrieve_supplements_from_dryad,      # Dryad dataset DOI files
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
    sources: list[str] | None = None,  # subset/reorder of ["europepmc","pmc_oa","dryad"]
    useful_kinds: Iterable[SupplementKind] = DEFAULT_USEFUL_KINDS,
    max_files: int = SUPPLEMENT_MAX_FILES,            # default 10
    max_file_bytes: int = SUPPLEMENT_MAX_FILE_BYTES,  # default 10 MB
    max_total_bytes: int = SUPPLEMENT_MAX_TOTAL_BYTES,  # default 50 MB
    max_archive_bytes: int = SUPPLEMENT_MAX_ARCHIVE_BYTES,  # default 100 MB
    max_text_chars: int = SUPPLEMENT_MAX_TEXT_CHARS,  # default 200_000
    save_dir: str | None = None,
) -> SupplementRetrievalResult
```

**Source order.** For a Dryad DOI (`10.5061/…`) only Dryad is tried. Otherwise
Europe PMC is tried first, then the NCBI PMC OA package as a fallback (reusing the
PMCID Europe PMC found). Retrieval stops at the first source with a kept file; if
none succeed, the result of the first source that *found* candidates is returned
so its `skipped`/`error` detail is preserved.

Each source also has a standalone function with the same caps (see imports above).
`retrieve_supplements_from_pmc_oa` takes a `pmcid` (plus optional `doi=`).

All caps are also configurable via `NMDC_SUPPLEMENT_*` environment variables
(see `constants.py`). Downloads are streamed and size-bounded; oversized files are
skipped by reported size *before* being read, and no archive is buffered beyond
`max_archive_bytes`.

## Return: `SupplementRetrievalResult`

**Import path:** `nmdc_metadata_suggestor_ai_tool.models.supplement`

| Field | Type | Description |
|---|---|---|
| `doi` | `str` | Normalized DOI. |
| `source` | `str \| None` | Winning source: `"europepmc"`, `"pmc_oa"`, or `"dryad"`. |
| `pmcid` | `str \| None` | PMC id when available. |
| `files` | `list[SupplementFile]` | Kept high-value supplements. |
| `skipped` | `list[SupplementFile]` | Found but dropped (with `skipped_reason`). |
| `attempts` | `list[str]` | Sources tried, in order. |
| `error` | `str \| None` | Why nothing was kept (e.g. no OA supplements). |
| `has_supplements` | `bool` (property) | True when `files` is non-empty. |

### `SupplementFile`

| Field | Type | Description |
|---|---|---|
| `filename` | `str` | Name within the archive / repository. |
| `kind` | `SupplementKind` | `tabular`, `document`, `image`, `media`, `sequence`, `archive`, `other`. |
| `size_bytes` | `int \| None` | Uncompressed size. |
| `caption` | `str \| None` | JATS/repository caption or description, when available. |
| `text` | `str \| None` | Inlined content for text-like files (csv/tsv/txt), truncated. |
| `saved_path` | `str \| None` | Temp-file path for non-text kept files (xlsx/pdf/docx). |
| `skipped_reason` | `str \| None` | Set only on entries in `result.skipped`. |

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
