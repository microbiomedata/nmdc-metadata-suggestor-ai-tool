#!/bin/bash
# Quick start script for NMDC Metadata Suggestor

set -e

echo "🚀 NMDC Metadata Suggestor - Quick Start"
echo "=========================================="
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "⚠️  uv not found. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo "✅ uv installed successfully"
    echo "⚠️  Please restart your shell or run: source $HOME/.cargo/env"
    exit 0
fi

echo "✅ uv is installed"

# Check if .env exists
if [ ! -f .env ]; then
    echo "📝 Creating .env file from .env.example..."
    cp .env.example .env
    echo "⚠️  Please edit .env and add your API keys"
    echo ""
fi

# Install dependencies
echo "📦 Installing dependencies..."
uv sync --all-extras

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit .env and add your API keys (OPENAI_API_KEY or ANTHROPIC_API_KEY)"
echo "  2. Run the application: make run (or: uv run nmdc-suggestor)"
echo "  3. Run tests: make test (or: uv run pytest)"
echo "  4. See all available commands: make help"
echo ""
echo "Docker commands:"
echo "  - Build: make docker-build"
echo "  - Run: make docker-run"
echo "  - Dev environment: make docker-dev"
echo ""
