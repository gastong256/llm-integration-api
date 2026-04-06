.PHONY: help setup lint format test check precommit run up down ps logs demo bench train
.DEFAULT_GOAL := help

COMPOSE := docker compose
STACK ?= base
ARGS ?=

ifeq ($(STACK),obs)
COMPOSE_FILES := -f docker-compose.yml -f docker-compose.observability.yml
else ifeq ($(STACK),base)
COMPOSE_FILES :=
else
$(error STACK must be 'base' or 'obs')
endif

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

check: ## Run lint and tests
	$(MAKE) lint
	$(MAKE) test

precommit: ## Run all pre-commit hooks
	uv run pre-commit run --all-files

run: ## Run FastAPI app locally with reload
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --no-access-log

up: ## Start docker stack (STACK=base|obs, ARGS="--build -d")
	$(COMPOSE) $(COMPOSE_FILES) up $(ARGS)

down: ## Stop docker stack (STACK=base|obs, ARGS="--remove-orphans")
	$(COMPOSE) $(COMPOSE_FILES) down $(ARGS)

ps: ## Show docker stack status (STACK=base|obs, ARGS="")
	$(COMPOSE) $(COMPOSE_FILES) ps $(ARGS)

logs: ## Tail docker logs (STACK=base|obs, ARGS="--no-color --tail=120 app")
	$(COMPOSE) $(COMPOSE_FILES) logs $(ARGS)

demo: ## Run the walkthrough runner (ARGS="--auto --scene 3")
	uv run python scripts/demo.py $(ARGS)

bench: ## Run Locust benchmark (ARGS="--headless -u 8 -r 2 -t 20s --host http://localhost:8000")
	uv run locust -f scripts/locustfile.py $(ARGS)

train: ## Regenerate models/v1.joblib and models/v2.joblib
	uv run python scripts/train_models.py
