---
name: pdf-ingestion
description: Use this skill to retrieve PDF content for publications via web_fetch (agentic path).
---

# PDF Ingestion Skill

Use this skill when you need to retrieve PDF content for publications.

## Agentic path (default)

Use the built-in `web_fetch` tool directly — no download or temp file needed:

1. **Get a PDF URL** — from `SourceRetrievalResult.publication_urls` (DOI ingestion)
2. **Fetch it** — call `web_fetch` with the URL; the agent reads the content inline
3. **Use the content** — add it to your evidence; no cleanup required

For the programmatic (non-agentic) path using `download_pdf_to_tempfile`, see [refs/programmatic.md](refs/programmatic.md).
