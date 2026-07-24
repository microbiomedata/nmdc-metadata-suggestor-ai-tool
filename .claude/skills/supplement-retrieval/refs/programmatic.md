# Supplement Retrieval — Programmatic API

**Import path:** `nmdc_metadata_suggestor_ai_tool.publication_ingestion.retrieve_supplements`

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.retrieve_supplements import (
    retrieve_supplements,                 # generic entry point (alias)
    retrieve_supplements_from_europepmc,  # concrete Europe PMC implementation
    find_supplement_source_europepmc,     # availability check, no download
    classify_supplement,                  # filename -> SupplementKind
    DEFAULT_USEFUL_KINDS,                  # {TABULAR, DOCUMENT}
)
```

## `retrieve_supplements(doi, ...)`

```python
retrieve_supplements(
    doi: str,
    *,
    useful_kinds: Iterable[SupplementKind] = DEFAULT_USEFUL_KINDS,
    max_files: int = SUPPLEMENT_MAX_FILES,            # default 10
    max_file_bytes: int = SUPPLEMENT_MAX_FILE_BYTES,  # default 10 MB
    max_total_bytes: int = SUPPLEMENT_MAX_TOTAL_BYTES,  # default 50 MB
    max_archive_bytes: int = SUPPLEMENT_MAX_ARCHIVE_BYTES,  # default 100 MB
    max_text_chars: int = SUPPLEMENT_MAX_TEXT_CHARS,  # default 200_000
    save_dir: str | None = None,
) -> SupplementRetrievalResult
```

All caps are also configurable via `NMDC_SUPPLEMENT_*` environment variables
(see `constants.py`). Downloads are streamed and size-bounded; the archive is
never buffered beyond `max_archive_bytes`.

## Return: `SupplementRetrievalResult`

**Import path:** `nmdc_metadata_suggestor_ai_tool.models.supplement`

| Field | Type | Description |
|---|---|---|
| `doi` | `str` | Normalized DOI. |
| `source` | `str \| None` | Retrieval source (`"europepmc"`). |
| `pmcid` | `str \| None` | PMC id when available. |
| `files` | `list[SupplementFile]` | Kept high-value supplements. |
| `skipped` | `list[SupplementFile]` | Found but dropped (with `skipped_reason`). |
| `error` | `str \| None` | Why nothing was kept (e.g. no OA supplements). |
| `has_supplements` | `bool` (property) | True when `files` is non-empty. |

### `SupplementFile`

| Field | Type | Description |
|---|---|---|
| `filename` | `str` | Name within the archive. |
| `kind` | `SupplementKind` | `tabular`, `document`, `image`, `media`, `sequence`, `archive`, `other`. |
| `size_bytes` | `int \| None` | Uncompressed size. |
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

## Availability check (no download)

```python
info = find_supplement_source_europepmc(doi)
# {"source", "article_id", "pmcid", "is_open_access", "has_supplements", "zip_url", "error"?}
```

Only attempts a download when `has_supplements` is true (Europe PMC `hasSuppl == "Y"`),
avoiding wasted requests for articles with no open-access supplements.
