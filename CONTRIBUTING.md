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
   uv sync
   
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
uv sync

# Create .env file
cp .env.example .env
# Edit .env and add your API keys
```

### Code Quality

Before submitting a pull request, ensure your code passes all quality checks:

```bash
# Run all checks at once
make lint
make test

# Or individually:
uv run ruff format     # Format code
uv run ruff check      # Lint code
uv run mypy src        # Type check
uv run pytest          # Run tests
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
- Format code with Ruff (line length: 100)
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

## Contributing domain knowledge to the env-triad skill

You do not need to write Python to contribute here. If you work in soil, water,
sediment, plant, or another MIxS environment, the most valuable thing you can add
is knowledge about how samples from that environment should be described.

### Where to write

One file per MIxS extension, in
`.claude/skills/env-triad/refs/extensions/`. Find yours from the roster in
[`refs/extensions.md`](.claude/skills/env-triad/refs/extensions.md). Each file has
four sections; the first two are already filled in, the last two are for you:

| Section | What belongs there |
|---|---|
| Where candidates come from | Which value set or ENVO subtrees apply. Usually already correct — tell us if it is not. |
| Evidence in the sample record | Which sample fields carry the signal for this environment. |
| **Domain notes** | How a specialist actually decides. Distinctions the terms do not make obvious, common misclassifications, what a submitter's wording usually means. |
| **Worked examples** | Concrete evidence → value. "Study says X, sample field says Y, so `env_medium` is `<term>` and not `<other term>`, because …" |

Worked examples are the highest-value thing you can write. One good example
teaches more than a paragraph of rules.

Keep it specific to your environment. Anything that holds across all twelve
extensions — how a slot is validated, which ontologies a slot allows — belongs in
[`refs/validation.md`](.claude/skills/env-triad/refs/validation.md) instead, so it
is not repeated twelve times.

### Look terms up, do not recall them

Every ENVO term you write is checked by `tests/test_env_triad_references.py`: it
must exist in the ENVO release oaklib is serving, must not be obsolete, must carry ENVO's
own label, and — when your line names a slot — must be the right kind of term for
that slot. Recalled CURIEs are wrong often enough that this check exists.

Find and confirm terms without leaving the repo:

```bash
uv run python -c "
from nmdc_metadata_suggestor_ai_tool.envo import get_envo
envo = get_envo()

# find a term
for term in envo.search('paddy soil', slot='env_medium'):
    print(term.value, '--', term.definition)

# confirm one you already have
print(envo.validate('soil [ENVO:00001998]', 'env_medium', 'SoilInterface'))
"
```

Write terms in full as `label [ENVO:NNNNNNN]`. `term.value` prints exactly that
form, so copy it verbatim rather than retyping the label — a recalled label on a
real CURIE is the most common way this goes wrong.

If you need to name a term as one to *avoid* — something the schema still offers
but ENVO has removed — add it to `DOCUMENTED_COUNTEREXAMPLES` in the test with a
comment saying why.

### Before opening the PR

```bash
uv run pytest tests/test_env_triad_references.py
```

That is the only suite your change affects. A failure names the file, the line,
and what to do about it.

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