# nmdc-metadata-suggestor

A Python application for the NMDC Submission Portal metadata suggestor tool, powered by AI.

This repository includes a DOI resolution pipeline that can:
- validate and classify DOIs
- retrieve dataset or publication context text
- route repository-backed DOIs through provider-specific resolvers
- fall back through generic scholarly metadata sources
- collect linked publication metadata when a provider exposes it reliably

## Prerequisites

- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) for dependency management, or Docker for containerized development
- Docker and Docker Compose if you want the container workflow

## Quick Start

### Option 1: Local Development With `uv`

1. Install `uv`:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Clone the repository:
   ```bash
   git clone https://github.com/microbiomedata/nmdc-metadata-suggestor-ai-tool.git
   cd nmdc-metadata-suggestor-ai-tool
   ```

3. Install dependencies:
   ```bash
   uv sync
   ```

4. Configure environment:
   ```bash
   cp .env.example .env
   ```

5. Run the application:
   ```bash
   uv run nmdc-suggestor
   ```

### Option 2: Docker

1. Clone the repository:
   ```bash
   git clone https://github.com/microbiomedata/nmdc-metadata-suggestor-ai-tool.git
   cd nmdc-metadata-suggestor-ai-tool
   ```

2. Configure environment:
   ```bash
   cp .env.example .env
   ```

3. Run the development container:
   ```bash
   docker-compose up
   ```

4. Or build and run the production image:
   ```bash
   docker build -t nmdc-suggestor .
   docker run --env-file .env nmdc-suggestor
   ```

## DOI Resolution

The DOI pipeline lives under `src/nmdc_metadata_suggestor/doi_ingestion/`.

Its main entry point is `get_doi_description_or_abstract()` in [src/nmdc_metadata_suggestor/doi_ingestion/main.py](/home/harry/nmdc-metadata-suggestor-ai-tool/src/nmdc_metadata_suggestor/doi_ingestion/main.py).

### What It Returns

A DOI lookup produces a `SourceRetrievalResult` with these main fields:

- `context`: cleaned abstract or description text
- `raw_context`: the original text before cleaning
- `context_type`: usually `abstract`, `description`, or another source-specific label
- `provider`: inferred repository/provider, such as `figshare` or `ess-dive`
- `source`: the source that ultimately supplied the winning text
- `attempts`: ordered list of sources attempted
- `publication_urls`: linked publication/document URLs when available
- `publication_dois`: linked publication DOIs when available and different from the requested DOI
- `source_errors`: per-source errors captured during the waterfall
- `error`: top-level error when no usable context was found

### Resolution Flow

For a DOI lookup, the pipeline does the following:

1. Normalize the DOI into a canonical form.
2. Infer the likely provider from DOI prefix or source metadata.
3. Choose a source order.
4. Query each source in order until one returns usable text.
5. Preserve publication metadata collected earlier in the waterfall if later sources provide the text.
6. Return the winning text plus accumulated publication metadata and source diagnostics.

### Waterfall Behavior

The source order depends on what kind of DOI the system believes it is.

For repository-backed DOIs, the resolver usually tries the provider-specific source first, then falls back to generic scholarly metadata sources:

1. Provider resolver such as `figshare`, `ess-dive`, `massive`, `zenodo`, `osti`, `edi`, `emsl`, `jgi`, `kbase`, or `cyverse`
2. `datacite`
3. `crossref`
4. `content_negotiation`
5. `openalex`
6. `pubmed`

If the provider is unknown, the pipeline starts with the generic sources directly.

You can also bypass the default waterfall and force specific sources by passing `sources=[...]` to `get_doi_description_or_abstract()` or `sources=...` through the DOI CLI.

### Generic Sources

The generic fallback sources contribute the following:

| Source | What it checks | What it can return |
|---|---|---|
| `datacite` | DataCite `descriptions` | Abstract or description text |
| `crossref` | Crossref `message.abstract` | Abstract text |
| `content_negotiation` | DOI content negotiation `citeproc+json` | Abstract text, or note/description fallback |
| `openalex` | OpenAlex `abstract_inverted_index` | Reconstructed abstract text |
| `pubmed` | DOI -> PMID -> PubMed efetch | Plain-text abstract |

## Provider Resolvers

These provider resolvers are currently wired into the DOI waterfall:

- `osti`
- `edi`
- `emsl`
- `ess-dive`
- `figshare`
- `jgi`
- `kbase`
- `massive`
- `cyverse`
- `zenodo`

### Provider Capabilities

| Provider | Primary API or lookup path | Context text currently retrieved | Publication metadata currently retrieved |
|---|---|---|---|
| `osti` | OSTI E2 API, with fallback to legacy OSTI API | Record `description` | Yes. Full-text URLs from journal-article `links[].href` and linked journal-article DOIs |
| `edi` | EDI DOI API -> PASTA EML metadata | `abstract`, then `purpose` / `description` / `title` | No. Disabled until a stable live publication-metadata example is confirmed |
| `emsl` | ScienceCentral `study/light`, then EMSL external projects API | `abstract`, then `title` / `project_type` | No. Disabled until a stable live publication-metadata example is confirmed |
| `ess-dive` | ESS-DIVE packages API, then public DataONE Solr/object fallback | `abstract` or `description` from package metadata or public DataONE EML | Yes. Publication links/DOIs from package metadata, plus related-reference DOIs from public DataONE EML |
| `figshare` | Figshare articles API, then collections API, with detail fetch when needed | Record or detail `description` | Yes. Document URLs from `files` and related publication DOIs from `resource_doi` / references |
| `jgi` | JGI search API by project id and DOI | `abstract`, `lay_description`, `description`, `title`, `project_name`, or `name` | No. Disabled until a stable live publication-metadata example is confirmed |
| `kbase` | KBase narrative search API, then workspace object lookup | Narrative descriptions, titles, workspace metadata summaries | No. Disabled until a stable live publication-metadata example is confirmed |
| `massive` | DOI redirect -> PROXI dataset lookup -> landing-page HTML fallback | Dataset description text, title/subtitle fallback from DataCite when needed | Yes. Publication links/DOIs from PROXI, plus publication DOIs from the MassIVE landing page `Publications` block |
| `cyverse` | CyVerse Terrain metadata search, then Data Commons metadata | AVU-based abstract/description/title fields | No. Disabled until a stable live publication-metadata example is confirmed |
| `zenodo` | Zenodo records API queried by DOI and concept DOI | Record `metadata.description` | Yes. File URLs from `files` and publication DOIs from `metadata.related_identifiers` |

### Provider Notes

- `osti`: OSTI records are not always the publication itself. The resolver checks journal-article entries linked from the OSTI record and captures full-text URLs and linked publication DOIs.
- `emsl`: The resolver prefers ScienceCentral for context text. If ScienceCentral already returns usable text, it does not spend time calling the external projects API.
- `ess-dive`: Anonymous ESS-DIVE API access is not always available. The resolver can fall back to public DataONE metadata and still recover related-reference DOIs from EML.
- `massive`: When PROXI is incomplete, the resolver can still recover publication DOIs from the public MassIVE landing page.
- `edi`, `emsl`, `jgi`, `kbase`, `cyverse`: These providers currently return context text only. Publication-link discovery is intentionally disabled because we do not yet have stable, confirmed live examples providing publication metadata in a consistent manner.

## DOI CLI

The DOI CLI lives in [src/nmdc_metadata_suggestor/cli/doi_cli.py](/home/harry/nmdc-metadata-suggestor-ai-tool/src/nmdc_metadata_suggestor/cli/doi_cli.py).

Useful commands:

```bash
# Validate a DOI
uv run python -m nmdc_metadata_suggestor.cli.doi_cli validate 10.1038/s41564-020-00861-0

# Classify a DOI
uv run python -m nmdc_metadata_suggestor.cli.doi_cli classify 10.1038/s41564-020-00861-0

# Fetch context using the default waterfall
uv run python -m nmdc_metadata_suggestor.cli.doi_cli get-abstract 10.15485/2588483

# Force a specific source order
uv run python -m nmdc_metadata_suggestor.cli.doi_cli get-abstract 10.25345/C5SZ1P sources=massive,datacite
```

## Development

### Project Structure

```text
nmdc-metadata-suggestor-ai-tool/
├── src/
│   └── nmdc_metadata_suggestor/
│       ├── cli/
│       │   └── doi_cli.py
│       ├── doi_ingestion/
│       │   ├── main.py
│       │   ├── doi_utils.py
│       │   ├── datacite.py
│       │   ├── crossref.py
│       │   ├── content_negotiation.py
│       │   ├── openalex.py
│       │   ├── pubmed.py
│       │   ├── osti.py
│       │   ├── edi.py
│       │   ├── emsl.py
│       │   ├── ess_dive.py
│       │   ├── figshare.py
│       │   ├── jgi.py
│       │   ├── kbase.py
│       │   ├── massive.py
│       │   ├── cyverse.py
│       │   └── zenodo.py
│       ├── models/
│       │   ├── doi.py
│       │   ├── resolver_context.py
│       │   └── schema.py
│       ├── publication_ingestion/
│       ├── llm_client.py
│       ├── main.py
│       ├── recommendation_pipeline.py
│       ├── schema_context.py
│       └── system_prompt.py
├── tests/
├── docs/
├── scripts/
├── pyproject.toml
├── Dockerfile
├── Dockerfile.dev
├── docker-compose.yml
└── README.md
```

### Running Tests

```bash
# Full test suite
uv run pytest

# DOI ingestion tests only
uv run pytest tests/test_doi_ingestion.py

# Live integration tests only
uv run pytest -m integration

# Coverage
uv run pytest --cov=src/nmdc_metadata_suggestor
```

### Code Quality

```bash
uv run ruff check src tests
uv run mypy src
```

### Adding Dependencies

```bash
uv add package-name
uv add --dev package-name
uv sync
```

## Configuration

Configuration is managed with environment variables or a `.env` file. See `.env.example` for the complete set.

Examples:

- `DEFAULT_MODEL`
- `MAX_TOKENS`
- `TEMPERATURE`

## Docker Development Workflow

### Interactive Development

```bash
docker-compose up -d
docker-compose exec app uv run pytest
docker-compose exec app bash
docker-compose down
```

### Production Build

```bash
docker build -t nmdc-suggestor:latest .
docker run --env-file .env nmdc-suggestor:latest
```

## Contributing

1. Create a feature branch.
2. Make the change.
3. Run tests and lint.
4. Open a pull request.

## License

[Specify your license here]
