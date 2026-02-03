# nmdc-metadata-suggestor

A Python application for the NMDC Submission portal metadata suggestor tool, powered by AI. This project uses modern Python tooling with [uv](https://github.com/astral-sh/uv) for dependency management and Docker for containerization.

## Features

- 🚀 Modern Python development with uv
- 🐳 Docker and Docker Compose support
- 🤖 LLM integration (OpenAI and Anthropic)
- ⚙️ Configuration management with Pydantic Settings
- 📝 Structured logging with Loguru
- 🧪 Testing setup with pytest
- 🔧 Code quality tools (Black, Ruff, MyPy)

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

5. **Run the application**:
   ```bash
   uv run nmdc-suggestor
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
│   └── nmdc_metadata_suggestor/
│       ├── __init__.py
│       ├── main.py           # Main entry point
│       ├── config.py          # Configuration management
│       └── llm_client.py      # LLM client interface
├── tests/                     # Test files
├── config/                    # Additional configuration files
├── pyproject.toml            # Project dependencies and metadata
├── Dockerfile                # Production Docker image
├── Dockerfile.dev            # Development Docker image
├── docker-compose.yml        # Docker Compose configuration
├── .env.example              # Example environment variables
└── README.md                 # This file
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

## Using the LLM Client

The application includes a flexible LLM client that supports both OpenAI and Anthropic:

```python
from nmdc_metadata_suggestor.llm_client import LLMClient

# Initialize client (defaults to OpenAI)
client = LLMClient(provider="openai")

# Generate a response
response = client.generate("Your prompt here")
print(response)

# Use Anthropic instead
client = LLMClient(provider="anthropic")
response = client.generate("Your prompt here", model="claude-3-5-sonnet-20241022")

# Async usage
response = await client.generate_async("Your prompt here")
```

## Configuration

Configuration is managed through environment variables or a `.env` file. See `.env.example` for available options:

- `OPENAI_API_KEY`: Your OpenAI API key
- `ANTHROPIC_API_KEY`: Your Anthropic API key
- `DEFAULT_MODEL`: Default LLM model to use
- `MAX_TOKENS`: Maximum tokens for LLM responses
- `TEMPERATURE`: Temperature for LLM responses (0.0-1.0)
- `LOG_LEVEL`: Logging level (DEBUG, INFO, WARNING, ERROR)
- `DEBUG`: Enable debug mode (true/false)

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

[Specify your license here]

## Acknowledgments

This project uses:
- [uv](https://github.com/astral-sh/uv) for fast Python package management
- [OpenAI](https://openai.com/) and [Anthropic](https://www.anthropic.com/) for LLM capabilities
- [Pydantic](https://docs.pydantic.dev/) for data validation
- [Loguru](https://github.com/Delgan/loguru) for logging

