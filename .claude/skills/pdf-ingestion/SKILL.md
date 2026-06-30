---
name: pdf-ingestion
description: Use this skill to retrieve PDF content for publications — via web_fetch in the agentic path, or download_pdf_to_tempfile in the programmatic path.
---

# PDF Ingestion Skill

Use this skill when you need to retrieve PDF content for publications.

## Agentic path (default)

The Claude Agent SDK provides a built-in `web_fetch` server-side tool. Use it directly — no download or temp file needed:

1. **Get a PDF URL** — from `SourceRetrievalResult.publication_urls` (DOI ingestion)
2. **Fetch it** — call `web_fetch` with the URL; the agent reads the content inline
3. **Use the content** — add it to your evidence; no cleanup required

Do not use `download_pdf_to_tempfile` in the agentic path.

---

## Programmatic path (non-agentic only)

When calling `ConversationManager.generate()` directly (not `agentic()`), the model cannot fetch URLs itself. In that case:

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

**Import path:** `nmdc_metadata_suggestor_ai_tool.publication_ingestion.retreive_pdf_link`

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.retreive_pdf_link import (
    retrieve_pdf_link_from_crossref,
    retrieve_pdf_link_from_pmc,
)
```

| Function | Returns |
|---|---|
| `retrieve_pdf_link_from_crossref(id)` | `{"pdf_links": [...]}` |
| `retrieve_pdf_link_from_pmc(doi)` | `{"pdf_url": str \| None, "pmcid": str \| None}` |
