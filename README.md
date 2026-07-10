# nmdc-metadata-suggestor-ai-tool

A Python application for the NMDC Submission portal metadata suggestor tool, powered by AI. This project uses modern Python tooling with [uv](https://github.com/astral-sh/uv) for dependency management and Docker for containerization.

## Prerequisites

- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) (or use Docker)
- Docker and Docker Compose (for containerized development)

## Quick Start

### LLM Configuration:
You will need to set up a `.env` file. Copy the example first:

```bash
cp .env.example .env
```

Environment variables used by `LLMClient` and `ConversationManager`:

- `AI_INCUBATOR_KEY`: API key for PNNL AI Incubator (when using `access_provider=pnnl`).
- `AI_INCUBATOR_BASE_URL`: Base URL for the PNNL AI Incubator API.
- `GOOGLE_APPLICATION_CREDENTIALS`: Path to a GCP service account JSON file (for Vertex AI).
- `VERTEX_PROJECT_ID`: (Optional) GCP project id for Vertex. If not provided, the SDK will attempt to infer it from credentials.
- `GEMINI_REGION`: (Optional) Vertex region override for Gemini calls (falls back to `CLOUD_ML_REGION`, then `us-east5`).
- `CLOUD_ML_REGION`: (Optional) Default Vertex region for `access_provider=gcp` (used as the fallback when `GEMINI_REGION` is unset; defaults to `us-east5`).
- `CBORG_KEY`: API key for CBORG (when using `access_provider=cborg`).
- `CBORG_BASE_URL`: Base URL for the CBORG API.

The `LLMClient` will read the appropriate variables depending on `access_provider` (set to `pnnl`, `cborg`, or `gcp`).

Environment variables are loaded from a `.env` file in the project root via
[python-dotenv](https://saurabh-kumar.com/python-dotenv/). Variables already
set in your shell take precedence over `.env` values (`override=False` is the
default).

### Using uv (Local Development)

1. **Install uv** (if not already installed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   # or
   pip install uv
   ```

2. **Clone and setup**:
   ```bash
   git clone https://github.com/microbiomedata/nmdc-metadata-suggestor-ai-tool.git
   cd nmdc-metadata-suggestor-ai-tool
   ```

3. **Install dependencies**:
   ```bash
   uv sync
   ```

4. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env and add your API keys
   ```

5. **Use the package in Python**:
   ```bash
   uv run python
   ```

   ```python
   from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient
   from nmdc_metadata_suggestor_ai_tool.recommendation_pipeline import run_recommendation_pipeline

   submission_object = {
       # NMDC submission JSON payload
   }

   client = LLMClient(access_provider="gcp")
   result = run_recommendation_pipeline(submission_object, client)
   print(result.model_dump())
   ```

Advanced: direct `ConversationManager` usage (optional)

```python
from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient, ConversationManager

client = LLMClient(access_provider="gcp")
conversation = ConversationManager(llm_client=client)
# Add plain text context (pdf_files may be a list of local PDF paths)
conversation.add_message(text="Please summarize the submission.", pdf_files=None)
# Add any schema context to guide the model
conversation.add_schema_context("<schema description here>")
response = conversation.generate(model="gemini-2.5-flash", max_tokens=1024, gemini_temperature=0.2)
print(response)
```

## Development

### Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/nmdc_metadata_suggestor_ai_tool

# Run specific test file
uv run pytest tests/test_recommendation_pipeline.py
```

### Code Quality

```bash
# Format code with Ruff
uv run ruff format

# Lint with Ruff
uv run ruff check

# Type check with MyPy
uv run mypy src
```

### Adding Dependencies

```bash
# Add a production dependency
uv add package-name

# Add a development dependency
uv add --dev package-name

# Update dependencies
uv sync
```

## Configuration

Configuration is managed through environment variables or a `.env` file. See `.env.example` for all options. Beyond the LLM access variables above:

- `CONTACT_EMAIL`: Contact email sent in User-Agent headers for the Crossref polite pool and OpenAlex (defaults to `support@microbiomedata.org`).

Advanced ingestion tuning (all optional; defaults shown):

- `NMDC_EDI_MAX_XML_CHARS`: Max characters read from an untrusted EDI metadata XML payload (default `2000000`).
- `NMDC_DATAONE_SOLR_MAX_XML_CHARS`: Max characters read from an untrusted DataONE Solr XML payload (default `2000000`).
- `NMDC_HTTP_RETRY_ATTEMPTS`: Retry attempts for publication/DOI HTTP requests (default `3`).
- `NMDC_HTTP_RETRY_BACKOFF_SECONDS`: Base backoff in seconds between retries (default `0`).
- `NMDC_HTTP_MAX_RETRY_DELAY_SECONDS`: Cap in seconds on retry delay (default `30`).
- `NMDC_HTTP_POOL_CONNECTIONS`: HTTP connection pool size (default `20`).
- `NMDC_HTTP_POOL_MAXSIZE`: HTTP connection pool max size (default `100`).

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests and quality checks
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## License

See [LICENSE](LICENSE) for licensing terms.


