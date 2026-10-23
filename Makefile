# PrecioLocal ER — atajos de desarrollo
# Requiere docker compose v2 y, para test/lint, python 3.12 (ver target `venv`).

PYTHON ?= python
SCRAPER_DIR = services/scraper

.PHONY: up down logs ps health db-migrate db-status db-reset db-roles db-setup db-rotate-secrets test lint clean test-scraper lint-scraper scrape train demo purge-demo smoke venv e2e e2e-install e2e-data

up:          ## Levanta la stack completa (postgres, redis, mongo, scraper, ml-dotnet, web)
	docker compose up -d

venv:        ## Crea el venv de Python del scraper DENTRO de WSL (Linux) y lo instala
	@wsl -e bash -c 'set -e; V=$$HOME/.venvs/pl; mkdir -p $$HOME/.venvs; \
		if [ ! -f $$V/bin/python ]; then python3 -m venv --without-pip $$V; fi; \
		if ! $$V/bin/python -m pip --version >/dev/null 2>&1; then \
			curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py; \
			$$V/bin/python /tmp/get-pip.py --quiet; \
		fi; \
		cd "$$(wslpath -u "$$(pwd)")/$(SCRAPER_DIR)"; $$V/bin/python -m pip install -q -e ".[test,dev]"; \
		echo "venv listo: $$V"'

wsl-python:  ## Shell de Python del venv WSL (usar para correr pytest/ruff/mypy a mano)
	@wsl -e bash -c 'exec $$HOME/.venvs/pl/bin/python -i'

down:        ## Detiene la stack
	docker compose down

logs:        ## Logs de la stack
	docker compose logs -f

ps:          ## Estado de los servicios
	docker compose ps

health:      ## Espera a que los 6 servicios estén healthy
	@until [ "$$(docker compose ps --format json | grep -c '"Health":"healthy"')" -eq 6 ]; do sleep 2; done; docker compose ps

db-migrate: ## Aplica las migraciones pendientes usando el ledger (FORCE=1 reaplica todas)
	@for f in db/supabase/migrations/*.sql; do \
		v=$$(basename $$f .sql); \
		applied=$$(docker compose exec -T postgres psql -At -q -U $${POSTGRES_USER:-preciolocal} -d $${POSTGRES_DB:-preciolocal} -c "SELECT 1 FROM schema_migrations WHERE version='$$v'" 2>/dev/null); \
		if [ -z "$(FORCE)" ] && [ "$$applied" = "1" ]; then \
			echo ">> skip  $$v (ya aplicada)"; \
		else \
			echo ">> apply $$v"; \
			docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U $${POSTGRES_USER:-preciolocal} -d $${POSTGRES_DB:-preciolocal} -f /migrations/$$(basename $$f) || exit 1; \
			docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U $${POSTGRES_USER:-preciolocal} -d $${POSTGRES_DB:-preciolocal} -c "INSERT INTO schema_migrations (version) VALUES ('$$v') ON CONFLICT (version) DO NOTHING" || exit 1; \
		fi; \
	done

db-status:  ## Migraciones aplicadas según el ledger vs los archivos del repo
	@echo "-- aplicadas en la DB:"; \
	docker compose exec -T postgres psql -At -U $${POSTGRES_USER:-preciolocal} -d $${POSTGRES_DB:-preciolocal} -c "SELECT version FROM schema_migrations ORDER BY version"; \
	echo "-- archivos en el repo:"; \
	@for f in db/supabase/migrations/*.sql; do basename $$f .sql; done

db-reset:  ## DESTRUCTIVO: borra el schema public y reaplica todo (CONFIRM=1)
	@if [ "$(CONFIRM)" != "1" ]; then echo "abortado: falta CONFIRM=1 (esto borra todos los datos)"; exit 1; fi; \
	docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U $${POSTGRES_USER:-preciolocal} -d $${POSTGRES_DB:-preciolocal} -c "DROP SCHEMA public CASCADE" -c "CREATE SCHEMA public"; \
	$(MAKE) db-migrate FORCE=1

db-roles:  ## Crea/verifica los roles LOGIN de servicio (P0.1). Rotar: scripts/db-rotate-roles.sh
	@scripts/db-service-roles.sh

# Orden correcto en una base nueva: migraciones (crean los grupos NOLOGIN
# pl_scraper/pl_app/pl_ml/pl_analytics y las policies) DESPUÉS de los roles LOGIN. Al revés,
# la primera vez que la app conecta no existe y falla con "password
# authentication failed", que parece un problema de credenciales y no de orden.
db-setup: db-migrate db-roles

db-rotate-secrets:  ## Rota passwords de los roles de servicio y el ADMIN_TOKEN
	@scripts/db-rotate-roles.sh all

test: test-scraper
lint: lint-scraper

test-scraper: ## Tests del scraper (85 casos: parsers, normalización, dedupe, ML, API)
	cd $(SCRAPER_DIR) && $(PYTHON) -m pytest -q

lint-scraper: ## Lint + tipos del scraper (ruff + mypy)
	cd $(SCRAPER_DIR) && $(PYTHON) -m ruff check app tests && $(PYTHON) -m mypy app

scrape:      ## Scrape manual contra el scraper ya levantado (ADMIN_TOKEN + stack up)
	curl -s -X POST localhost:8000/scrape/run/$${CHAIN:-la_anonima} -H "X-Admin-Token: $$ADMIN_TOKEN"

train:       ## Entrena y persiste forecasts + anomalías + métricas (requiere stack up)
	$(PYTHON) scripts/train_forecasts.py

demo:        ## Genera 60 días de histórico sintético DEMO-* (requiere stack up)
	$(PYTHON) scripts/seed_demo_history.py

purge-demo:  ## Borra los datos DEMO- y sus forecasts
	$(PYTHON) scripts/seed_demo_history.py --purge

smoke:       ## Smoke E2E local (pega contra la DB; scrape real contra los sitios)
	$(PYTHON) scripts/smoke_e2e.py

e2e-install: ## Descarga el Chromium de Playwright (una sola vez)
	cd web && npx playwright install chromium

e2e-data:    ## Deja datos E2E listos: 60 días DEMO-* + forecasts de los 2 modelos
	$(MAKE) demo
	$(MAKE) train

e2e:         ## E2E de navegador contra la stack real (requiere `up`, demo y train)
	cd web && npx playwright test

clean:       ## Baja y borra volúmenes de datos
	docker compose down -v