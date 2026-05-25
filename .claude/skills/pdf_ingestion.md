# PDF Ingestion Skill

Use this skill when you need to find and download PDFs for publications, then clean them up after use.

## Flow overview

1. **Get a PDF URL** — from `SourceRetrievalResult.publication_urls` (DOI ingestion) or by calling the retrieval helpers directly.
2. **Download to a temp file** — `download_pdf_to_tempfile(url)` returns a local path.
3. **Pass the path to the LLM** — add it to the conversation as a PDF attachment.
4. **Clean up** — call `remove_temp_files(paths)` after the LLM call.

---

## Downloading PDFs

**Import path:** `nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf`

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import (
    download_pdf_to_tempfile,
    remove_temp_files,
    remove_temp_file,
)
```

### `download_pdf_to_tempfile(url) -> str`

Downloads the PDF at `url` to a temporary file and returns its path.

- Uses `curl_cffi` with Chrome TLS impersonation for Cloudflare-protected domains (e.g. `pubs.acs.org`). Falls back to plain `requests` for all other domains.
- Validates the downloaded file starts with the `%PDF-` magic bytes.
- Raises `RuntimeError` on failure (HTTP error, non-PDF response, network issue). The temp file is cleaned up automatically on failure.

```python
try:
    pdf_path = download_pdf_to_tempfile(url)
    # use pdf_path ...
except RuntimeError as e:
    logger.warning(f"Could not download PDF: {e}")
```

### `remove_temp_files(paths: list[str]) -> None`

Removes a list of temp file paths. Silently ignores missing files and logs exceptions rather than raising.

### `remove_temp_file(path: str) -> None`

Single-file variant of the above.

---

## Finding PDF links

When `SourceRetrievalResult.publication_urls` is populated by DOI ingestion, use those directly.
For additional lookup by DOI or Crossref ID, use the retrieval helpers:

**Import path:** `nmdc_metadata_suggestor_ai_tool.publication_ingestion.retreive_pdf_link`

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.retreive_pdf_link import (
    retrieve_pdf_link_from_crossref,
    retrieve_pdf_link_from_pmc,
)
```

### `retrieve_pdf_link_from_crossref(id) -> dict`

Fetches PDF links from the Crossref API for a given DOI or publication ID.

Returns `{"pdf_links": [...]}` on success, or `{"error": "...", "pdf_links": []}` on failure.

### `retrieve_pdf_link_from_pmc(doi) -> dict`

Looks up a publication in Europe PMC using its DOI, extracts open-access PDF URLs or constructs a PMC PDF URL.

Returns `{"pdf_url": str | None, "pmcid": str | None}`, with an optional `"error"` key on failure.

---

## Full example

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import (
    download_pdf_to_tempfile,
    remove_temp_files,
)

pdf_files = []
for url in publication_links:
    try:
        pdf_path = download_pdf_to_tempfile(url)
        pdf_files.append(pdf_path)
    except RuntimeError:
        logger.exception(f"Failed to download PDF from {url}")

# ... use pdf_files with the LLM ...

# always clean up
remove_temp_files(pdf_files)
```
