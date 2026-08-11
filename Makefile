.PHONY: help dev-install test test-integration lint lint-fix format security check-deps clean docker-build docker-run docker-dev validate-doi classify-doi classify-fixture get-abstract get-abstracts build-envo-index

help: ## Show this help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

all-install: ## Install all dependencies including dev
	uv sync

prod-install: ## Install production dependencies
	uv sync --no-dev

dev-install: ## Install only dev dependencies
	uv sync --only-dev

test: ## Run unit tests (mocked, no network)
	uv run pytest

test-integration: ## Run integration tests against real APIs
	uv run pytest -m integration

lint: ## Run linters
	uv run ruff check
	uv run mypy src 

format: ## This is a two part command that both checks, then auto-fixes formatting issues where it can.
	uv run ruff format
	uv run ruff check --fix

# Audit the synced project environment directly. Auditing a requirements file
# instead makes pip-audit build a temp venv, which crashes (ensurepip SIGABRT)
# on uv's managed standalone Python (python-build-standalone). Environment mode
# never creates a venv, so it works across platforms and Python distributions.
security: ## Audit dependencies for known CVEs (pip-audit)
	uv run --with pip-audit pip-audit --progress-spinner off

check-deps: ## Check dependency hygiene: unused/missing/misplaced dependencies (deptry)
	uv run --with deptry deptry src

# The only place this repo fetches ontology data. Runtime never hits the network:
# envo_index.py reads the committed artifact. Review the envo_version and term-count
# diff before committing a refresh.
build-envo-index: ## Rebuild the pinned ENVO index from the current ENVO release
	uv run python scripts/build_envo_index.py

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
