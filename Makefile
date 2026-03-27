.PHONY: setup lint format test precommit run up down train help
.DEFAULT_GOAL := help

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## Install dependencies and sync environment
	uv sync --all-extras

lint: ## Run ruff linter
	uv run ruff check app/ tests/

format: ## Run ruff formatter
	uv run ruff format app/ tests/

test: ## Run pytest suite
	uv run pytest

precommit: ## Run all pre-commit hooks
	uv run pre-commit run --all-files

run: ## Run FastAPI app locally with reload
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

up: ## Build and start docker containers
	docker-compose up --build

down: ## Stop and remove docker containers
	docker-compose down

train: ## Regenerate models/v1.joblib and models/v2.joblib
	uv run python scripts/train_models.py
