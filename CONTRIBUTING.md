# Contributing to NMDC Metadata Suggestor

Thank you for your interest in contributing to the NMDC Metadata Suggestor! This document provides guidelines and instructions for contributing.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/nmdc-metadata-suggestor-ai-tool.git
   cd nmdc-metadata-suggestor-ai-tool
   ```
3. **Install uv** (if not already installed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   # Restart your shell or run: source $HOME/.cargo/env
   ```
4. **Set up your environment**:
   ```bash
   # Install dependencies
   uv sync --all-extras
   
   # Create .env file from template
   cp .env.example .env
   # Edit .env and add your API keys (OPENAI_API_KEY or ANTHROPIC_API_KEY)
   ```
5. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Workflow

### Setting Up Your Environment

Using uv (recommended):
```bash
# Install dependencies
uv sync --all-extras

# Create .env file
cp .env.example .env
# Edit .env and add your API keys
```

Using Docker:
```bash
# Start development environment
docker-compose up

# Or for interactive development
docker-compose up -d
docker-compose exec app bash
```

### Code Quality

Before submitting a pull request, ensure your code passes all quality checks:

```bash
# Run all checks at once
make lint
make test

# Or individually:
uv run black src tests          # Format code
uv run ruff check src tests     # Lint code
uv run mypy src                 # Type check
uv run pytest                   # Run tests
```

### Testing

- Write tests for new features
- Ensure existing tests pass
- Aim for good test coverage
- Use pytest for testing

```bash
# Run tests
make test

# Run tests with coverage
make test-cov

# Run specific test file
uv run pytest tests/test_specific.py
```

### Code Style

- Follow PEP 8 style guide
- Use type hints for function signatures
- Format code with Black (line length: 100)
- Use Ruff for linting
- Write descriptive docstrings

Example:
```python
def process_metadata(data: dict[str, Any]) -> dict[str, str]:
    """
    Process metadata from input data.

    Args:
        data: Raw input data dictionary

    Returns:
        Processed metadata as a string dictionary
    """
    # Implementation here
    pass
```

## Pull Request Process

1. **Update documentation** if you're adding features or changing behavior
2. **Add tests** for new functionality
3. **Run all quality checks** and ensure they pass
4. **Write a clear PR description** explaining:
   - What changes you made
   - Why you made them
   - How to test them

### PR Checklist

- [ ] Code follows the project's style guidelines
- [ ] Tests added/updated and passing
- [ ] Documentation updated
- [ ] Commit messages are clear and descriptive
- [ ] No merge conflicts with main branch

## Reporting Issues

When reporting issues, please include:

- A clear, descriptive title
- Steps to reproduce the issue
- Expected behavior
- Actual behavior
- Your environment (OS, Python version, etc.)
- Relevant logs or error messages

## Questions?

If you have questions:
- Check the README.md
- Search existing issues
- Open a new issue with the "question" label
- Reach out to the maintainers