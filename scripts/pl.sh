#!/usr/bin/env bash
# PrecioLocal ER — atajos de desarrollo para WSL / Linux / git-bash.
#
# Por qué existe: el `Makefile` cubre los mismos targets, pero `make` no está
# instalado en la máquina de desarrollo Windows (ver CONTEXTO.md §7, entorno).
# Este script no necesita nada más que bash + docker compose, y es lo que se
# usa en la práctica. Los dos caminos hacen lo mismo; si instalás `make`,
# preferí el Makefile.
#
# Uso:  ./scripts/pl.sh <target> [ARG=valor ...]
#   up | down | logs | ps | health
#   migrate [FORCE=1] | db-status | db-reset CONFIRM=1
#   db-roles | db-setup | db-rotate-secrets [all|db|token]
#   train | demo | purge-demo | smoke | scrape CHAIN=la_anonima
#   venv | gates | web-test | web-build | e2e-install | e2e-data | e2e
#
# E2E de navegador (Playwright): necesita la stack levantada (`./scripts/pl.sh up`)
# y datos comparables entre cadenas. Los productos scrapeados son de una sola
# cadena, así que la comparación multi-cadena la da `demo` + `train`:
#   ./scripts/pl.sh e2e-install   # una vez, descarga el Chromium
#   ./scripts/pl.sh up && ./scripts/pl.sh e2e-data && ./scripts/pl.sh e2e

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SCRAPER_DIR="services/scraper"
WEB_DIR="web"
# venv de Python: Linux/WSL (preferido) o Windows (fallback).
VENV_PY="${VENV_PY:-$HOME/.venvs/pl/bin/python}"
if [ ! -x "$VENV_PY" ]; then
  VENV_PY="${VENV_PY_WIN:-/mnt/c/Users/vparr/AppData/Local/Temp/opencode/pl-venv/Scripts/python.exe}"
fi
py() {
  if [ -x "$VENV_PY" ]; then echo "$VENV_PY"; else echo "python3"; fi
}

psql_app() {
  docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 \
    -U "${POSTGRES_USER:-preciolocal}" -d "${POSTGRES_DB:-preciolocal}" "$@"
}

target="${1:-help}"; shift || true

case "$target" in
  up)     docker compose up -d ;;
  down)   docker compose down ;;
  logs)   docker compose logs -f ;;
  ps)     docker compose ps ;;
  clean)  docker compose down -v ;;

  health)
    echo "esperando 6 servicios healthy..."
    until [ "$(docker compose ps --format json 2>/dev/null | grep -c '"Health":"healthy"')" -eq 6 ]; do
      sleep 2
    done
    docker compose ps
    ;;

  migrate)
    force="${FORCE:-}"
    for f in db/supabase/migrations/*.sql; do
      v=$(basename "$f" .sql)
      applied=$(docker compose exec -T postgres psql -At -q \
        -U "${POSTGRES_USER:-preciolocal}" -d "${POSTGRES_DB:-preciolocal}" \
        -c "SELECT 1 FROM schema_migrations WHERE version='$v'" 2>/dev/null)
      if [ -z "$force" ] && [ "$applied" = "1" ]; then
        echo ">> skip  $v (ya aplicada)"
      else
        echo ">> apply $v"
        docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 \
          -U "${POSTGRES_USER:-preciolocal}" -d "${POSTGRES_DB:-preciolocal}" \
          -f "/migrations/$(basename "$f")" || exit 1
        docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 \
          -U "${POSTGRES_USER:-preciolocal}" -d "${POSTGRES_DB:-preciolocal}" \
          -c "INSERT INTO schema_migrations (version) VALUES ('$v') ON CONFLICT (version) DO NOTHING" || exit 1
      fi
    done
    ;;

  db-status)
    echo "-- aplicadas en la DB:"
    psql_app -At -c "SELECT version FROM schema_migrations ORDER BY version"
    echo "-- archivos en el repo:"
    for f in db/supabase/migrations/*.sql; do basename "$f" .sql; done
    ;;

  db-reset)
    if [ "${CONFIRM:-}" != "1" ]; then
      echo "abortado: falta CONFIRM=1 (esto borra todos los datos)"; exit 1
    fi
    psql_app -c "DROP SCHEMA public CASCADE" -c "CREATE SCHEMA public"
    FORCE=1 "$0" migrate
    ;;

  db-roles)
    "$REPO_ROOT/scripts/db-service-roles.sh"
    ;;

  # Orden correcto en una base nueva: las migraciones crean los grupos NOLOGIN
  # (pl_scraper/pl_app/pl_ml/pl_analytics) y las policies; los roles LOGIN se crean después.
  # Al revés, la app falla al primer arranque con "password authentication
  # failed", que parece de credenciales y es de orden.
  db-setup)
    "$0" migrate
    "$0" db-roles
    ;;

  db-rotate-secrets)
    "$REPO_ROOT/scripts/db-rotate-roles.sh" "${1:-all}"
    ;;

  venv)
    v="$HOME/.venvs/pl"
    mkdir -p "$HOME/.venvs"
    [ -f "$v/bin/python" ] || python3 -m venv --without-pip "$v"
    if ! "$v/bin/python" -m pip --version >/dev/null 2>&1; then
      curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
      "$v/bin/python" /tmp/get-pip.py --quiet
    fi
    (cd "$SCRAPER_DIR" && "$v/bin/python" -m pip install -q -e ".[test,dev]")
    echo "venv listo: $v"
    ;;

  gates)
    (cd "$SCRAPER_DIR" && "$(py)" -m ruff check app tests) || exit 1
    (cd "$SCRAPER_DIR" && "$(py)" -m mypy app) || exit 1
    (cd "$SCRAPER_DIR" && "$(py)" -m pytest -q) || exit 1
    ;;

  test)  (cd "$SCRAPER_DIR" && "$(py)" -m pytest -q) ;;
  lint)  (cd "$SCRAPER_DIR" && "$(py)" -m ruff check app tests && "$(py)" -m mypy app) ;;

  web-test)  (cd "$WEB_DIR" && npx ng test --watch=false --browsers=ChromeHeadless) ;;
  web-build) (cd "$WEB_DIR" && npx ng build) ;;

  e2e-install) (cd "$WEB_DIR" && npx playwright install chromium) ;;
  e2e-data)
    "$0" demo || exit 1
    "$0" train || exit 1
    ;;
  e2e) (cd "$WEB_DIR" && npx playwright test ${HEADED:+--headed} ${UI:+--ui} ${GREP:+--grep "$GREP"}) ;;

  train)
    (cd "$REPO_ROOT" && "$(py)" scripts/train_forecasts.py ${CHAIN:+--chain "$CHAIN"} ${LIMIT:+--limit "$LIMIT"} ${DRY_RUN:+--dry-run})
    ;;
  demo)
    (cd "$REPO_ROOT" && "$(py)" scripts/seed_demo_history.py ${ALLOW_REMOTE:+--allow-remote})
    ;;
  purge-demo)
    (cd "$REPO_ROOT" && "$(py)" scripts/seed_demo_history.py --purge)
    ;;
  smoke)
    (cd "$REPO_ROOT" && "$(py)" scripts/smoke_e2e.py)
    ;;
  scrape)
    chain="${CHAIN:-la_anonima}"
    curl -s -X POST "localhost:${SCRAPER_PORT:-8000}/scrape/run/$chain" \
      -H "X-Admin-Token: ${ADMIN_TOKEN:-}"
    echo
    ;;

  help|*)
    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac