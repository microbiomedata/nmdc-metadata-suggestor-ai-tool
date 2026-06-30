---
name: doi_ingestion
description: Use this skill to fetch abstract or description text for a DOI using a source waterfall (datacite, crossref, openalex, pubmed, and repository-specific sources like osti, ess-dive, jgi).
---

# DOI Ingestion Skill

Use this skill when you need to fetch abstract or description text for a DOI using the source waterfall.

## Main entry point

**Import path:** `nmdc_metadata_suggestor_ai_tool.doi_ingestion.main`

```python
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.main import get_doi_description_or_abstract

result = get_doi_description_or_abstract(doi="10.25585/1488209")
```

### Signature

```python
get_doi_description_or_abstract(
    doi: str,
    sources: list[str] | None = None,
    skip_classification: bool = False,
) -> SourceRetrievalResult
```

- **`doi`** — any common format: bare (`10.x/y`), URL (`https://doi.org/10.x/y`), or prefixed (`doi:10.x/y`). Normalized automatically.
- **`sources`** — explicit ordered list of sources to try. When omitted, the source order is inferred from the DOI's classification and provider.
- **`skip_classification`** — skip the DOI validation/classification gate (useful when the provider is already known, e.g. from submission metadata).

---

## Return value: `SourceRetrievalResult`

**Import path:** `nmdc_metadata_suggestor_ai_tool.models.doi`

Key fields:

| Field | Type | Description |
|---|---|---|
| `context` | `str \| None` | Abstract or description text (the primary payload). |
| `publication_urls` | `list[str] \| None` | URLs to the publication PDF or full text. |
| `publication_dois` | `list[str] \| None` | DOIs of linked publications. |
| `source` | `str \| None` | Which source returned the context. |
| `provider` | `str \| None` | Inferred repository/publisher (e.g. `"osti"`, `"ess-dive"`). |
| `attempts` | `list[str]` | All sources that were tried. |
| `source_errors` | `dict[str, str]` | Per-source error messages for failed attempts. |
| `error` | `str \| None` | Top-level error if the overall request failed. |

---

## Source waterfall

Sources are tried in order; the first to return `context` text wins.
Accumulated `publication_urls` are collected across all sources regardless of which one wins.

**Publication DOI sources (default order):** `datacite`, `crossref`, `content_negotiation`, `openalex`, `pubmed`

**Repository-specific sources (used when provider is inferred from the DOI prefix):**
`ess-dive`, `edi`, `emsl`, `figshare`, `jgi`, `kbase`, `massive`, `zenodo`, `cyverse`, `osti`

When a `provider` is passed (e.g. from submission metadata), it is placed first in the source list automatically.

---

## DOI utilities

**Import path:** `nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils`

```python
from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_utils import (
    normalize_doi,
    classify_doi,
    infer_provider_from_doi,
)
```

| Function | Purpose |
|---|---|
| `normalize_doi(doi)` | Strip URL/prefix wrappers and return bare `10.x/y` form. |
| `classify_doi(doi)` | Return a `DoiClassification` (registration agency, resource type, inferred NMDC category). |
| `infer_provider_from_doi(doi)` | Identify the repository provider from the DOI prefix. |

---

## Common usage pattern

```python
result = get_doi_description_or_abstract(
    doi=doi_value,
    skip_classification=True,  # skip when provider is already known
    sources=["osti"],           # pass provider as first source
)

if result.context:
    # use abstract/description text
    print(result.context)

if result.publication_urls:
    # PDF or full-text URLs available for download
    print(result.publication_urls)

if result.error:
    print(f"Failed: {result.error}")
    print(result.source_errors)
```
