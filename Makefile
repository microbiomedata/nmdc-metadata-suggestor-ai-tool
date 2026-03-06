.PHONY: help dev-install test test-integration lint lint-fix format clean docker-build docker-run docker-dev validate-doi classify-doi classify-fixture get-abstract get-abstracts

help: ## Show this help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

all-install:
	uv sync --all-extras

prod-install: ## Install production dependencies
	uv sync --no-dev

dev-install: ## Install all dependencies including dev
	uv sync --extra dev

test: ## Run unit tests (mocked, no network)
	uv run pytest

test-integration: ## Run integration tests against real APIs
	uv run pytest -m integration

lint: ## Run linters
	uv run ruff check src tests
	uv run mypy src

format: ## This is a two part command that both checks, then auto-fixes formatting issues where it can.
	uv run black src tests
	uv run ruff check --fix src tests

clean: ## Clean up generated files
	rm -rf .pytest_cache
	rm -rf .mypy_cache
	rm -rf .ruff_cache
	rm -rf htmlcov
	rm -rf dist
	rm -rf build
	rm -rf *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

docker-build: ## Build production Docker image
	docker build -t nmdc-suggestor:latest .

docker-dev-build: ## Build development Docker image
	docker build -f Dockerfile.dev -t nmdc-suggestor:dev .

docker-run: ## Run production Docker container
	docker run --env-file .env nmdc-suggestor:latest

docker-dev: ## Start development environment with Docker Compose
	docker-compose up

docker-dev-down: ## Stop development environment
	docker-compose down

docker-shell: ## Open shell in development container
	docker-compose exec app bash

run: ## Run the application locally
	uv run nmdc-suggestor

DOI_CLI = uv run python -m nmdc_metadata_suggestor.cli.doi_cli

validate-doi: ## Validate a DOI (usage: make validate-doi DOI=10.1038/s41564-020-00861-0)
	$(DOI_CLI) validate $(DOI)

classify-doi: ## Classify a DOI (usage: make classify-doi DOI=10.1038/s41564-020-00861-0)
	$(DOI_CLI) classify $(DOI)

classify-fixture: ## Classify all DOIs in the test fixture
	$(DOI_CLI) classify-fixture

get-abstract: ## Fetch abstract for a DOI (usage: make get-abstract DOI=10.1038/... [OUT=abstracts/] [SOURCES=crossref,pubmed])
	$(DOI_CLI) get-abstract $(DOI) $(OUT) $(if $(SOURCES),sources=$(SOURCES))

get-abstracts: ## Fetch abstracts for all publication DOIs in the fixture
	$(DOI_CLI) get-abstracts
