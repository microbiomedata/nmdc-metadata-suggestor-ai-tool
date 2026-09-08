# PDF Ingestion — Programmatic Path

Use this when calling `ConversationManager.generate()` directly (not `agentic()`). The model cannot fetch URLs itself, so PDFs must be downloaded to temp files first.

**Import path:** `nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf`

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import (
    download_pdf_to_tempfile,
    remove_temp_files,
)
```

1. **Download** — `download_pdf_to_tempfile(url)` returns a local path
2. **Pass to model** — `conversation_manager.add_message(text="...", pdf_files=[pdf_path])`
3. **Clean up** — `remove_temp_files(pdf_paths)` after the LLM call

```python
pdf_files = []
for url in publication_links:
    try:
        pdf_path = download_pdf_to_tempfile(url)
        pdf_files.append(pdf_path)
    except RuntimeError:
        logger.exception(f"Failed to download PDF from {url}")

# ... generate ...

remove_temp_files(pdf_files)
```

---

## Finding PDF links

When `SourceRetrievalResult.publication_urls` is populated by DOI ingestion, use those directly.
For additional lookup by DOI or Crossref ID:

**Import path:** `nmdc_metadata_suggestor_ai_tool.publication_ingestion.retrieve_pdf_link`

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.retrieve_pdf_link import (
    retrieve_pdf_link_from_crossref,
    retrieve_pdf_link_from_pmc,
)
```

| Function | Returns |
|---|---|
| `retrieve_pdf_link_from_crossref(id)` | `{"pdf_links": [...]}` |
| `retrieve_pdf_link_from_pmc(doi)` | `{"pdf_url": str \| None, "pmcid": str \| None}` |
