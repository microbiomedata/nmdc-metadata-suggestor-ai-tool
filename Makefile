.PHONY: help install dev-install test lint format clean docker-build docker-run docker-dev

help: ## Show this help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

install: ## Install production dependencies
	uv sync --no-dev

dev-install: ## Install all dependencies including dev
	uv sync

test: ## Run tests
	uv run pytest

test-cov: ## Run tests with coverage
	uv run pytest --cov=src/nmdc_metadata_suggestor --cov-report=html --cov-report=term

lint: ## Run linters
	uv run ruff check src tests
	uv run mypy src

format: ## Format code
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
