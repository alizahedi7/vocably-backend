.DEFAULT_GOAL := help
.PHONY: help install dev run migrate makemigration downgrade test lint format typecheck up down logs seed grant-admin partitions worker beat deck-validate deck-plan deck-build deck-sync-meta

# System tools (e.g. a sourced ROS environment) may export PYTHONPATH, which leaks their
# packages into uv's isolated venv and breaks pytest plugin autoload. Blank it for every
# recipe so commands run against the project's venv only.
export PYTHONPATH :=

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime + dev dependencies with uv
	uv sync --extra dev

run: ## Run the API with autoreload
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

migrate: ## Apply all Alembic migrations
	uv run alembic upgrade head

makemigration: ## Autogenerate a migration (usage: make makemigration m="message")
	uv run alembic revision --autogenerate -m "$(m)"

downgrade: ## Roll back one migration
	uv run alembic downgrade -1

seed: ## Seed the database with demo data
	uv run python -m app.scripts.seed

grant-admin: ## Grant admin to a user (usage: make grant-admin who="+989121234567" [revoke=1])
	uv run python -m app.scripts.grant_admin "$(who)" $(if $(revoke),--revoke,)

partitions: ## Roll the word_reviews partition window forward (add prune=1 to drop expired months)
	uv run python -m app.scripts.partition_word_reviews $(if $(prune),--prune,)

deck-validate: ## Check a deck template offline (usage: make deck-validate [slug=504-essential-words])
	uv run python -m app.scripts.build_deck validate $(slug)

deck-plan: ## Create the deck, units and item plan for a template (spends nothing)
	uv run python -m app.scripts.build_deck plan "$(slug)" $(if $(owner),--owner "$(owner)",)

deck-sync-meta: ## Re-apply a template's name/description/icon to its built deck (spends nothing)
	uv run python -m app.scripts.build_deck sync-meta "$(slug)"

deck-build: ## Resolve a planned build (usage: make deck-build job=<id> [queue=1])
	uv run python -m app.scripts.build_deck build "$(job)" $(if $(queue),--queue,)

worker: ## Run a Celery worker (background tasks)
	uv run celery -A app.tasks worker --loglevel=info -Q default,maintenance,ai

beat: ## Run Celery beat (the scheduler). Exactly one instance, ever.
	uv run celery -A app.tasks beat --loglevel=info

test: ## Run the test suite
	uv run pytest -q

lint: ## Lint with ruff
	uv run ruff check app tests

format: ## Auto-format with ruff
	uv run ruff format app tests
	uv run ruff check --fix app tests

typecheck: ## Static type checking with mypy
	uv run mypy app

up: ## Start Postgres + API via docker compose
	docker compose up --build

down: ## Stop docker compose services
	docker compose down

logs: ## Tail docker compose logs
	docker compose logs -f
