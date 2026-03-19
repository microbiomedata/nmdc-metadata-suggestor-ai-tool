# nmdc-metadata-suggestor-ai-tool

A Python application for the NMDC Submission portal metadata suggestor tool, powered by AI. This project uses modern Python tooling with [uv](https://github.com/astral-sh/uv) for dependency management and Docker for containerization.

## Prerequisites

- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) (or use Docker)
- Docker and Docker Compose (for containerized development)

## Quick Start

### Option 1: Using uv (Local Development)

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

### Option 2: Using Docker

1. **Clone the repository**:
   ```bash
   git clone https://github.com/microbiomedata/nmdc-metadata-suggestor-ai-tool.git
   cd nmdc-metadata-suggestor-ai-tool
   ```

2. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env and add your API keys
   ```

3. **Run with Docker Compose** (development):
   ```bash
   docker-compose up
   ```

4. **Or build and run production image**:
   ```bash
   docker build -t nmdc-suggestor .
   docker run --env-file .env nmdc-suggestor
   ```

## Development

### Project Structure

```
nmdc-metadata-suggestor-ai-tool/
├── src/
│   └── nmdc_metadata_suggestor_ai_tool/
│       ├── __init__.py
│       ├── recommendation_pipeline.py       # Pipeline orchestration
│       ├── llm_client.py                    # LLM client for AI interactions
│       ├── cli/
│       │   ├── __init__.py
│       │   └── doi_cli.py                   # DOI operations CLI
│       ├── models/
│       │   ├── __init__.py
│       │   ├── doi.py                       # DOI data models
│       │   └── llm_output.py                # LLM output model
│       └── publication_ingestion/
│           ├── __init__.py
│           ├── download_pdf.py              # PDF retrieval logic
│           └── retreive_pdf_link.py         # PDF link discovery
├── tests/                                    # Test files
├── scripts/                                  # Vertex AI test scripts
├── docs/                                     # Documentation
├── pyproject.toml                            # Project dependencies and metadata
├── Dockerfile                                # Production Docker image
├── Dockerfile.dev                            # Development Docker image
├── docker-compose.yml                        # Docker Compose configuration
├── .env.example                              # Example environment variables
└── README.md                                 # This file
```

### Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/nmdc_metadata_suggestor

# Run specific test file
uv run pytest tests/test_example.py
```

### Code Quality

```bash
# Format code with Black
uv run black src tests

# Lint with Ruff
uv run ruff check src tests

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

Configuration is managed through environment variables or a `.env` file. See `.env.example` for available options:

- `DEFAULT_MODEL`: Default LLM model to use
- `MAX_TOKENS`: Maximum tokens for LLM responses
- `TEMPERATURE`: Temperature for LLM responses (0.0-1.0)

## Docker Development Workflow

### Interactive Development

For interactive development with hot-reload:

```bash
# Start container in background
docker-compose up -d

# Execute commands in the container
docker-compose exec app uv run pytest
docker-compose exec app uv run black src

# Access shell
docker-compose exec app bash

# Stop container
docker-compose down
```

### Production Build

```bash
# Build production image
docker build -t nmdc-suggestor:latest .

# Run production container
docker run --env-file .env nmdc-suggestor:latest
```

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


