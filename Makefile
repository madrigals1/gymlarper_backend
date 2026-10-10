.PHONY: help up down build restart logs shell test smoke migrations makemigrations \
        prod-up prod-down db backup superuser lint check clean \
        install install-dev lock

COMPOSE      := docker compose
PROD_COMPOSE := docker compose -f docker-compose.yml
IMAGE        := gymlarper-api:dev

# --- Python environment (pipenv) ------------------------------------------

install:  ## Create the venv and install locked runtime packages
	pipenv install --deploy

install-dev:  ## Same, but including [dev-packages]
	pipenv install --dev

lock:  ## Re-resolve dependencies and rewrite Pipfile.lock (run after editing Pipfile)
	pipenv lock

# --- Development -----------------------------------------------------------

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up:  ## Start the dev stack (API + Postgres, live reload)
	$(COMPOSE) up -d --build
	@echo "API:  http://127.0.0.1:$${API_PORT:-8000}"
	@echo "Docs: http://127.0.0.1:$${API_PORT:-8000}/api/v1/"

down:  ## Stop the dev stack (keeps data)
	$(COMPOSE) down

build:  ## Rebuild the API image
	$(COMPOSE) build

restart:  ## Restart the API
	$(COMPOSE) restart api

logs:  ## Tail API logs
	$(COMPOSE) logs -f api

shell:  ## Django shell
	$(COMPOSE) exec api python manage.py shell

db:  ## psql prompt
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-gymlarper} -d $${POSTGRES_DB:-gymlarper}

superuser:  ## Create a Django admin user
	$(COMPOSE) exec api python manage.py createsuperuser

# --- Quality ---------------------------------------------------------------

test:  ## Run the test suite (SQLite, no server required)
	$(COMPOSE) run --rm api python manage.py test \
		--settings=config.test_settings -v 2

check:  ## Django system checks + confirm no missing migrations
	$(COMPOSE) exec api python manage.py check --deploy --fail-level ERROR
	$(COMPOSE) exec api python manage.py makemigrations --check --dry-run

lint:  ## Byte-compile everything as a cheap syntax gate
	$(COMPOSE) run --rm api python -m compileall -q apps config

smoke:  ## End-to-end HTTP smoke test against the running stack
	@echo "This needs a server with Google verification stubbed."
	@echo "See docs/API.md and ask for the local smoke wrapper."
	$(COMPOSE) run --rm api python scripts/smoke_test.py \
		--base-url http://api:8000 --id-token "$${GOOGLE_ID_TOKEN:-}"

migrations:  ## Apply migrations
	$(COMPOSE) exec api python manage.py migrate --noinput

makemigrations:  ## Generate migrations after changing models
	$(COMPOSE) run --rm -u root api python manage.py makemigrations

# --- Production ------------------------------------------------------------

prod-up:  ## Start the production stack (gunicorn + Caddy), skips dev overrides
	$(PROD_COMPOSE) --profile prod up -d --build

prod-down:  ## Stop the production stack
	$(PROD_COMPOSE) --profile prod down

backup:  ## Dump Postgres to a timestamped file
	@mkdir -p backups
	$(COMPOSE) exec -T db pg_dump -U $${POSTGRES_USER:-gymlarper} \
		$${POSTGRES_DB:-gymlarper} | gzip > backups/gymlarper-$$(date +%F-%H%M).sql.gz
	@ls -lh backups/ | tail -3

clean:  ## Remove containers and the database volume (DESTRUCTIVE)
	@read -p "Delete the database volume too? [y/N] " ok && [ "$$ok" = y ] || exit 1
	$(COMPOSE) down -v
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +