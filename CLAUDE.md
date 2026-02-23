# Project Instructions

## Setup
After cloning, run the `/secure-repo` slash command to install local secret scanning hooks (gitleaks + trufflehog). See `docs/secret-scanning.md` for details.

## Development
- Use `uv` for dependency management
- Run tests: `uv run pytest`
- Format: `uv run black src tests`
- Lint: `uv run ruff check src tests`
