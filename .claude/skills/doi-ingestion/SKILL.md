---
name: doi-ingestion
description: Use this skill to fetch abstract or description text for a DOI using a source waterfall (datacite, crossref, openalex, pubmed, and repository-specific sources like osti, ess-dive, jgi).
---

# DOI Ingestion Skill

Call `get_doi_description_or_abstract(doi, skip_classification, sources)` to fetch abstract or description text for a DOI.

Key return fields: `result.context` (abstract text), `result.publication_urls` (PDF/full-text URLs), `result.error`.

Pass `skip_classification=True` when the provider is already known (e.g. from submission metadata). Pass the provider string as the first element of `sources` to prioritize it.

For full API reference (signature, all return fields, source waterfall, utilities), see [refs/api.md](refs/api.md).
