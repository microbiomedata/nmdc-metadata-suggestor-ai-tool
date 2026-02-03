# Use official Python image as base
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml .
COPY .python-version .
COPY uv.lock .
COPY README.md .

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy application code
COPY src ./src

# Production stage
FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy from builder
COPY --from=builder /app /app

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

# Run the application
CMD ["uv", "run", "nmdc-suggestor"]
