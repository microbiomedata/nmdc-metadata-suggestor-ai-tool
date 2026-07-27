# DOI Classification Design

**Date:** 2026-02-17
**Branch:** `1597-publisher-api-abstract-text`
**Issues:** #1597 (abstract text), #1598 (PDF bytes)

## Summary

This document describes how we classify DOIs for NMDC metadata ingestion.
A DOI can point to a publication, dataset, award, or other resource — and
the same DOI may be registered with Crossref, DataCite, or another agency.
We classify along 5 orthogonal axes (type, agency, publisher, content level,
fetching mechanism), map external resource types to NMDC's 4-value
`DoiCategoryEnum`, and use the result to gate abstract retrieval so we
don't waste API calls on non-publication DOIs.

## Table of Contents

1. [Classification Axes](#1-classification-axes)
2. [Value Provenance](#2-value-provenance-what-is-canonical-vs-ad-hoc)
3. [Relationship to Olivia's Branch](#3-relationship-to-olivias-branch)
4. [API Credentials and Environment Variables](#4-api-credentials-and-environment-variables)
5. [Test Fixture](#5-test-fixture)
6. [CLI / Makefile Targets](#6-cli--makefile-targets)
7. [Abstract Retrieval Waterfall](#7-abstract-retrieval-waterfall)
8. [Full Text Retrieval — Scope and Limitations](#8-full-text-retrieval--scope-and-limitations)

---

## 1. Classification Axes

DOIs can be categorized along 5 independent (orthogonal) axes. Any combination
is possible — e.g., a publication DOI registered with Crossref from Nature where
you can get an abstract but not full text, fetched via content negotiation.

| # | Axis | Question | Source of values | Implemented? |
|---|------|----------|-----------------|-------------|
| 1 | **Resource Type** | What does the DOI point to? | Crossref `type` (30 values), DataCite `resourceTypeGeneral` (33 values) | Yes — `doi_utils.classify_doi()` |
| 2 | **Registration Agency** | Who issued the DOI? | `doi.org/doiRA/` API | Yes — `doi_utils.detect_registration_agency()` |
| 3 | **Publisher/Source** | Who publishes the content? | Crossref/DataCite metadata `publisher` field | Yes — returned by `classify_doi()` |
| 4 | **Fetchable Content Level** | What can you get back? | Ad-hoc (see below) | Not yet |
| 5 | **Fetching Mechanism** | How do you resolve it? | Ad-hoc (see below) | Not yet |

### Axis 4: Fetchable Content Levels (ad-hoc)

These levels are an analytical framework, not from any standard:

| Level | What you get | Typical availability |
|-------|-------------|---------------------|
| L1 | Metadata (title, authors, dates, type) | ~100% of valid DOIs |
| L1a | Abstract text | ~30% via Crossref, ~86% via PubMed |
| L2 | Full text (PDF or HTML) | ~44-52% via open access |
| L3 | Supplements, data files | <10% |

### Axis 5: Fetching Mechanisms (ad-hoc)

| Mechanism | Scope | API key needed? |
|-----------|-------|----------------|
| DOI content negotiation | Any DOI (Crossref, DataCite, mEDRA) | No |
| Crossref API | Crossref DOIs | No (polite pool with `mailto:` header) |
| DataCite API | DataCite DOIs | No |
| PubMed / Europe PMC | Publications with PMIDs | No |
| Unpaywall | OA full text discovery | Yes (email) |
| Publisher-specific APIs | Varies by publisher | Varies |

---

## 2. Value Provenance: What Is Canonical vs Ad-Hoc

### Externally canonical (from standards bodies)

| Values | Authority | Reference |
|--------|-----------|-----------|
| Crossref `type` field (30 values) | Crossref | https://api.crossref.org/types |
| DataCite `resourceTypeGeneral` (33 values) | DataCite | DataCite Metadata Schema 4.6 (Dec 2024) |
| Registration agency names (Crossref, DataCite, mEDRA, ...) | DOI Foundation | https://doi.org/doiRA/ |
| Handle API response codes (1, 2, 100, 200) | DONA Foundation | RFC 3650 (Handle System) |
| `publisher` strings | Individual publishers | Returned by Crossref/DataCite APIs |

### Internally canonical (nmdc-schema)

| Values | Definition | Reference |
|--------|-----------|-----------|
| `DoiCategoryEnum`: `award_doi`, `dataset_doi`, `publication_doi`, `data_management_plan_doi` | `nmdc-schema/src/schema/basic_slots.yaml` | https://microbiomedata.github.io/nmdc-schema/DoiCategoryEnum/ |

The schema includes `see_also` links to the DataCite PDF (resourceTypeGeneral,
pp 48-53) and `https://api.crossref.org/types`, plus a comment: "See especially
the resourceTypeGeneral section of the DataCite PDF."

### Ad-hoc (defined in this repo)

| What | Where | Rationale |
|------|-------|-----------|
| **Type→Category mapping sets** (`CROSSREF_PUBLICATION_TYPES`, `DATACITE_PUBLICATION_TYPES`, etc.) | `doi_utils.py` | nmdc-schema does NOT define which external types map to which DoiCategoryEnum value. Historically, NMDC assigned `doi_category` manually per-DOI in schema migrators (e.g., `migrator_from_8_1_to_9_0`). Our mapping automates this. |
| **5-axes framework** | This document | Analytical framing to avoid conflating independent concerns (e.g., publisher vs registration agency vs resource type). |
| **Content levels (L1/L1a/L2/L3)** | This document | Shorthand for what a fetching pipeline can expect to retrieve. |
| **`DoiClassification` model** | `models/doi.py` | Pydantic model combining axes 1-3 plus NMDC category inference. Not from any standard. |

### What the ad-hoc mappings deliberately exclude

Types that could plausibly map to an NMDC category but where the mapping is
ambiguous are left unmapped (return `None`). These require human review:

**Unmapped Crossref types:** `component`, `standard`, `report-component`,
`report-series`, `book-set`, `book-series`, `book-track`, `edited-book`,
`reference-book`, `journal`, `journal-volume`, `journal-issue`, `proceedings`,
`proceedings-series`, `other`, `database`

**Unmapped DataCite types:** `Software`, `Workflow`, `ComputationalNotebook`,
`Instrument`, `PhysicalObject`, `Collection`, `Image`, `Audiovisual`, `Sound`,
`Model`, `Service`, `Event`, `InteractiveResource`, `Standard`, `Journal`,
`ConferenceProceeding`, `DataPaper`, `StudyRegistration`, `Project`, `Other`

---

## 3. Relationship to Olivia's Branch

Olivia's PR #4 (`1599-create-api-access-to-the-osti-api`) defines:

- `models/publication.py` — a `Publication` model with fields: `source`,
  `osti_doi`, `publication_doi`, `pmid`, `urls`, `abstract`
- `publication_ingestion/osti_publication_retriever.py` — OSTI → Crossref →
  Europe PMC waterfall
- `publication_ingestion/download_pdf.py` — PDF download to tempfile

Our branch (`1597-publisher-api-abstract-text`) adds:

- `publication_ingestion/doi_utils.py` — DOI validation, classification, NMDC
  category inference (operates upstream of Olivia's retrieval code)
- `cli/doi_cli.py` — CLI for interactive testing (separate from business logic)
- `models/doi.py` — `DoiCategory` enum, `DoiValidation`, `DoiClassification`, `AbstractResult` models
- `models/publication.py` — refined `Publication` model (see merge notes below)

### Publication model: merge strategy

Our `models/publication.py` refines Olivia's version. Changes:

| Olivia's field | Our field | Why |
|---------------|-----------|-----|
| `osti_doi` | `doi` | Not all DOIs come from OSTI |
| `publication_doi` | `associated_publication_doi` | Avoids collision with `"publication_doi"` as a DoiCategoryEnum value |
| *(new)* | `doi_category` | Carries NMDC DoiCategoryEnum |
| *(new)* | `registration_agency` | Routes to correct fetching mechanism |
| `Optional[str]` | `str \| None` | py312 style, ruff UP rules |
| `List[HttpUrl]` | `list[HttpUrl]` | py312 style, ruff UP rules |

All other fields (`source`, `pmid`, `urls`, `abstract`) are unchanged.

To merge: Olivia's callers that set `osti_doi` should set `doi` instead, and
callers that set `publication_doi` should set `associated_publication_doi`.
All other fields are additive (no breaking changes for code that only reads
those fields).

### Flow: how the pieces connect

```
DOI string
  │
  ├─ normalize_doi()           → bare DOI
  ├─ validate_doi()            → DoiValidation (exists? Handle API)
  ├─ classify_doi()            → DoiClassification (type, RA, publisher, NMDC category)
  │
  ├─ if doi_category == "publication_doi":
  │     └─ [abstract retrieval pipeline]  → Publication.abstract   (#1597)
  │     └─ [PDF retrieval pipeline]       → PDF bytes              (#1598)
  │
  └─ if doi_category != "publication_doi":
        └─ skip or handle gracefully (dataset, award, DMP)
```

---

## 4. API Credentials and Environment Variables

### Current state

All APIs used by `doi_utils.py` are unauthenticated. The only credential is a
contact email in the `User-Agent` header, used for the Crossref polite pool
(faster rate limits) and expected by OpenAlex.

### Environment variables

Defined in `.env.example`, loaded from environment (not `load_dotenv` — the
caller or Docker/CI is responsible for injecting them):

| Variable | Used by | Required? | Default |
|----------|---------|-----------|---------|
| `CONTACT_EMAIL` | `doi_utils.py` User-Agent, future OpenAlex calls | No | `support@microbiomedata.org` |
| `OPENALEX_API_KEY` | Future abstract retrieval (#1597) | No | *(none — email auth is sufficient for moderate usage)* |
| `UNPAYWALL_EMAIL` | Future PDF discovery (#1598) | For Unpaywall calls | *(none)* |
| `API_KEY` | LLM access (Olivia's existing code) | For LLM features | *(none)* |

### How credentials flow in each environment

| Environment | How env vars are set |
|-------------|---------------------|
| **Local dev** | `.env` file (gitignored) or `export` in shell |
| **Docker** | `docker run --env-file .env` (existing `make docker-run` target) or `docker-compose.yml` `env_file:` directive |
| **GitHub Actions** | Repository secrets → `env:` block in workflow YAML. Example: `env: { CONTACT_EMAIL: ${{ secrets.CONTACT_EMAIL }} }` |
| **Production (if deployed)** | Platform-specific secret injection (GCP Secret Manager, AWS SSM, Kubernetes secrets, etc.) |

### Design principle

Code reads `os.environ.get("VAR", "fallback")` directly. No `.env` file is
loaded at import time — the entry point or container is responsible for
providing environment variables. This keeps the library importable without
side effects and avoids `python-dotenv` path issues in Docker/CI.

---

## 5. Test Fixture

`tests/fixtures/doi_test_cases.json` contains 29 curated DOI test cases:
- 25 real NMDC DOIs covering all 23 prefixes in the inventory
- 4 bogus DOIs for negative testing
- Full provenance tracing to NMDC production MongoDB dump `20260116_020423`

See `tests/fixtures/README.md` for detailed provenance documentation.

---

## 6. CLI / Makefile Targets

| Target | What it does |
|--------|-------------|
| `make validate-doi DOI=...` | Validate a single DOI via Handle API |
| `make classify-doi DOI=...` | Classify a DOI (type, RA, publisher, NMDC category) |
| `make classify-fixture` | Classify all DOIs in the test fixture (hits real APIs) |
| `make test` | Run all unit tests (mocked, no network) |
| `make lint` | Run ruff + mypy |

**Note:** The `cli/doi_cli.py` module and Makefile targets exist for
development-time interactive testing. Business logic lives in
`publication_ingestion/doi_utils.py` and `abstract_retriever.py`;
the CLI is a thin wrapper. These targets may be removed once the DOI
triage layer is integrated into the API.

---

## 7. Abstract Retrieval Waterfall

**Module:** `publication_ingestion/abstract_retriever.py`

### Programmatic usage

```python
from nmdc_metadata_suggestor.publication_ingestion.abstract_retriever import (
    get_abstract,
    AbstractResult,
)

result: AbstractResult = get_abstract("10.1038/s41564-020-00861-0")
result.abstract  # str | None — the abstract text
result.source  # "openalex" | "crossref" | "pubmed" | "content_negotiation" | None
result.pmid  # str | None — PubMed ID (only if source was PubMed)
result.attempts  # ["openalex", "crossref"] — sources tried, in order
result.error  # str | None — why it was refused or not found
```

### Classification gate

Before hitting any API, `get_abstract()` validates the DOI and classifies it
via `classify_doi()`. If the DOI is invalid or not a `publication_doi`, the
function refuses gracefully:

```python
>>> get_abstract("not-a-doi").error
'Invalid DOI: Malformed DOI syntax'

>>> get_abstract("10.15485/1729719").error   # dataset DOI
'DOI is a dataset_doi, not a publication. Abstract retrieval is only supported for publication DOIs.'
```

To bypass the gate (e.g., if you've already classified the DOI):

```python
result = get_abstract(doi, skip_classification=True)
```

### Waterfall order (empirically validated)

| # | Source | Why | Coverage | Raw format |
|---|--------|-----|----------|------------|
| 1 | **OpenAlex** | Best coverage, longest abstracts, covers both Crossref & DataCite DOIs | ~90% of publications | `inverted_index` |
| 2 | **Crossref** | Fallback for DOIs not yet in OpenAlex | ~30% have abstracts | `jats_xml` or `plain_text` |
| 3 | **PubMed** | Catches Nature/Elsevier holdouts | ~86% biomedical coverage | `plain_text` |
| 4 | **Content negotiation** | Universal last resort (Citeproc JSON via doi.org) | Same data as Crossref but works for any RA | `jats_xml` or `citeproc_json` |

Each step returns as soon as an abstract is found. If all 4 fail, returns
`AbstractResult(abstract=None, error="No abstract found in any source")`.
Network errors at any step are caught and the waterfall continues to the next
source.

### Response format classification

The `content_format` field on `AbstractResult` records what raw format the
API returned, **before** our parsing converted it to plain text. This lets
downstream code know whether the text may have parsing artifacts:

| `content_format` | Source(s) | What we do | Potential artifacts |
|------------------|-----------|------------|-------------------|
| `inverted_index` | OpenAlex | Reconstruct text from `{word: [positions...]}` | May lose punctuation spacing; gap positions become empty strings |
| `jats_xml` | Crossref, content negotiation | Strip `<jats:p>`, `<jats:italic>`, etc. via XML parser; unescape HTML entities | Structured sub-sections within abstracts are flattened to a single string |
| `plain_text` | PubMed efetch, Crossref (when no tags present) | Return as-is (after `.strip()`) | None — text is already clean |
| `citeproc_json` | Content negotiation (when no JATS tags) | Return as-is | None — text is already clean |

**Why OpenAlex uses an inverted index:** The format is optimized for search
indexing — you can find which abstracts contain a given word without
reconstructing the full text, and it compresses well since common words
aren't stored repeatedly.  The tradeoff is that reconstruction is lossy:
punctuation attached to words (e.g. `"soil,"` at position 82) survives, but
any non-space whitespace or formatting in the original is lost.  This is why
`AbstractResult` exposes both `abstract` (reconstructed plain text) and
`raw_abstract` (the original JSON-serialized inverted index).

Detection is simple: if the raw string contains `<`, we classify it as
`jats_xml` and strip tags; otherwise it's `plain_text` (Crossref) or
`citeproc_json` (content negotiation).

### Parsing helpers (also importable)

```python
from nmdc_metadata_suggestor.publication_ingestion.abstract_retriever import (
    decode_inverted_abstract,
    strip_jats_xml,
)

# OpenAlex returns inverted index: {word: [positions...]}
text = decode_inverted_abstract({"Hello": [0], "world": [1]})
# → "Hello world"

# Crossref/content negotiation return JATS XML
text = strip_jats_xml("<jats:p>An <jats:italic>important</jats:italic> finding.</jats:p>")
# → "An important finding."
```

### CLI / Makefile equivalents

| Make target | Equivalent function call |
|-------------|------------------------|
| `make get-abstract DOI=10.1038/...` | `get_abstract("10.1038/...")` → prints `AbstractResult` as JSON |
| `make get-abstract DOI=10.1038/... OUT=abstracts/` | Same, but writes JSON to `abstracts/{doi_slug}.json` |
| `make get-abstracts` | Runs `get_abstract()` on all `publication_doi` entries in the test fixture |

The `abstracts/` output directory is gitignored.

---

## 8. Full Text Retrieval — Scope and Limitations

Full text retrieval is **not implemented** in this module and is deliberately
out of scope for the abstract retrieval waterfall. This section documents why,
and what challenges a future full-text module would face.

### Why abstracts are sufficient for metadata suggestion

Abstracts are ~200-400 words of structured, high-signal text. For NMDC metadata
suggestion (inferring sample type, environment, methodology), the abstract
contains the key information in a clean, parseable form. Full papers are
5,000-15,000 words, with most of the additional content (discussion, related
work, references) being low-signal for metadata extraction.

### Challenges that a full-text module would need to address

**Format heterogeneity.** Full text arrives in multiple formats, each requiring
a different parser:

| Format | Source | Quality |
|--------|--------|---------|
| JATS XML | PubMed Central (PMC) | Excellent — structured, section-labeled, free |
| Publisher XML | Elsevier (ScienceDirect), Springer (via API) | Good — but proprietary schemas, API keys required |
| HTML | Some OA publishers | Variable — needs scraping, layout-dependent |
| PDF | Universal fallback | Poor — see below |

**PDF parsing difficulties:**
- Multi-column layouts produce interleaved text
- Headers, footers, and page numbers bleed into body text
- Tables render as positioned text fragments, not structured data
- Equations appear as images or garbled Unicode
- Scanned PDFs require OCR (additional dependency, lower accuracy)
- Ligatures (fi, fl, ff) may map to wrong Unicode codepoints

**Paywall / access barriers:**
- Most Nature, Elsevier, Wiley, ACS articles are paywalled
- Unpaywall can find OA copies but covers only ~50-60% of arbitrary DOIs
- Institutional access would require proxy configuration or API keys
- Some publishers offer text-mining APIs but require institutional agreements

**Size and cost implications:**
- A full paper is 7K-20K tokens (vs ~500 tokens for an abstract)
- Processing hundreds of DOIs through an LLM at full-text length is expensive
- Most metadata signal is concentrated in the abstract + methods section
- Section extraction (to get only methods) circles back to the format problem

**Non-text content that doesn't survive extraction:**
- Tables — often the most metadata-rich part (sample descriptions, measurements)
- Supplementary materials — frequently separate PDFs or Excel files
- Figures and captions
- Chemical formulas, gene names with special notation

### If full text is needed later

The highest-value next step would be **structured JATS XML from PMC**, which is:
- Free and open access
- Cleanly structured with labeled sections (`<sec sec-type="methods">`)
- Parseable with standard XML tools
- Available for ~5 million articles via the PMC OA subset

This would be a separate retriever module with a different return type (sections
rather than a single string), and would complement rather than replace the
abstract retrieval waterfall.
