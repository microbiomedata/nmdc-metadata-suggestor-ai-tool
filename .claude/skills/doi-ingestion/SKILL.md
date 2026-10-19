---
name: doi-ingestion
description: Use this skill to fetch abstract or description text for a DOI using a source waterfall (datacite, crossref, openalex, pubmed, and repository-specific sources like osti, ess-dive, jgi).
---

# DOI Ingestion Skill

```python
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.main import get_doi_description_or_abstract

result = get_doi_description_or_abstract(doi, skip_classification=True, sources=["crossref"])
```

Key return fields: `result.context` (abstract text), `result.license` (CC license URL for copyright check), `result.publication_urls` (PDF/full-text URLs), `result.error`.

Pass `skip_classification=True` when the provider is already known (e.g. from submission metadata). Pass the provider string as the first element of `sources` to prioritize it.

For full API reference (signature, all return fields, source waterfall, utilities), see [refs/api.md](refs/api.md).
