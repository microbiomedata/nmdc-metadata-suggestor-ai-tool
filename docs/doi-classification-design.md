# DOI Classification Design

**Date:** 2026-02-17
**Branch:** `1597-publisher-api-abstract-text`
**Issues:** #1597 (abstract text), #1598 (PDF bytes)

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
| **`DoiClassification` model** | `doi_utils.py` | Pydantic model combining axes 1-3 plus NMDC category inference. Not from any standard. |

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
- `publication_ingestion/doi_cli.py` — CLI for interactive testing
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

## 5. CLI / Makefile Targets

| Target | What it does |
|--------|-------------|
| `make validate-doi DOI=...` | Validate a single DOI via Handle API |
| `make classify-doi DOI=...` | Classify a DOI (type, RA, publisher, NMDC category) |
| `make classify-fixture` | Classify all DOIs in the test fixture (hits real APIs) |
| `make test` | Run all unit tests (mocked, no network) |
| `make lint` | Run ruff + mypy |
