# services/scraper — Python 3.12 + FastAPI

Scraping + normalización + ML estadístico (Holt-Winters + robust z-score;
SARIMAX e IsolationForest solo con el extra `.[ml]`).

**Rol único (ver [ADR-0001](../../docs/adr/0001-multiples-lenguajes.md)):**
captura precios reales de las cadenas de Entre Ríos y produce
predicciones/anomalías. NO sirve reportes agregados ni alertas.

## Stack

Instalado (`requirements.lock`): `fastapi` · `uvicorn` · `httpx` · `pydantic` +
`pydantic-settings` · `structlog` · `asyncpg` · `orjson` · `beautifulsoup4` ·
`redis` (solo storage del rate limiter) · `slowapi`.

**No** se usa, aunque este README antes los declaraba: `playwright`,
`motor` (driver de Mongo), `pandas`, `rapidfuzz`, `apscheduler`, OpenTelemetry.

`numpy` · `statsmodels` · `scikit-learn` están declarados como el extra
opcional `.[ml]` en `pyproject.toml` pero **no están en `requirements.lock`**, así
que la imagen corre sin ellos. Esos extras son para experimentación local, no
para producción:

| Modelo de producción (imagen base) | Extra `.[ml]` (opcional) |
|---|---|
| Holt-Winters | SARIMAX (no es el default) |
| Robust z-score (mediana + MAD) | IsolationForest |
| Imagen chica, sin dependencias pesadas | ~200 MB extra |

`GET /ml/forecast/{id}` usa `model=holtwinters` por default y responde
`"model":"holtwinters"` sin `degraded_reason`. Si se pide explícitamente
`model=sarimax` sin el extra, devuelve Holt-Winters con un `degraded_reason`
explícito: nunca reporta como SARIMAX un modelo que no corrió. La decisión está
en [ADR-0006](../../docs/adr/0006-dos-modelos-ml.md).

## Endpoints

Verificado contra `GET :8000/openapi.json`:

```
GET  /health                              liveness
GET  /ready                               readiness (DB + Redis)
GET  /chains                              cadenas de la tabla chains
GET  /products?q=&chain=&limit=&offset=   búsqueda paginada ({products,total})
GET  /products/{product_id}               detalle + precios por cadena
GET  /products/{product_id}/history?span= histórico
GET  /ml/forecast/{product_id}?chain=&horizon=&model=  forecast (default holtwinters)
GET  /ml/anomalies/{product_id}?chain=    anomalías (robust z-score)
GET  /scrape                              estado del último scrape
POST /scrape/run/{chain}                  admin (X-Admin-Token) + rate limited
```

**No hay `POST /admin/retrain`.** El entrenamiento es el job
`scripts/train_forecasts.py`, que corre por fuera del proceso web a propósito:
entrenar bloquea y no debe competir con el request loop.

## Cadenas

| slug | Parser | Fixtures |
|---|---|---|
| `la_anonima` | `scrapers/la_anonima.py` | `aceite.html`, `harina.html` |
| `coto_digital` | `scrapers/coto_digital.py` | `aceite_girasol.json` |
| `carrefour` | `scrapers/carrefour.py` | `aceites_comunes.json` |
| `cooperativa_obrera` | ❌ sin scraper | — |
| `changomas` | `scrapers/changomas.py` | `changomas/arroz_0_49.json` |

## Estructura real

```
app/
  main.py            # FastAPI app + lifespan
  config.py          # pydantic-settings
  models.py          # schemas de entrada/salida
  state.py           # AppState: pool, redis, circuit breakers
  errors.py          # problem+json
  api/
    router.py        # rutas
    ratelimit.py     # slowapi + storage Redis
  scrapers/
    base.py          # scraper abstracto + robots.txt
    robots.py        # caché del robots.txt por host
    circuit_breaker.py
    la_anonima.py  coto_digital.py  carrefour.py
  pipelines/
    normalize.py     # unidades, tamaños, marcas
    dedupe.py        # hash de idempotencia
    runner.py        # orquestación scrape → persist
  ml/
    api.py           # endpoints /ml/* (bajo demanda)
    models.py        # holt_winters, robust z-score, sarimax (extra), walk-forward
    series.py        # construcción de la serie
  storage/
    postgres.py      # ProductRepository (asyncpg)
tests/
  unit/              # parsers, normalize, dedupe, ml, base scraper
  integration/       # test_api.py
  fixtures/          # HTML/JSON real por cadena
```

No hay `jobs/` (nada de APScheduler: el scrape se dispara por POST o a mano) ni
`storage/mongo.py` ni `storage/redis.py`.

## Comandos

```bash
# local
pip install -r requirements.lock && pip install --no-deps -e . && pip install -e ".[test,dev]"
pytest -q                  # 368 tests, 90% de cobertura
ruff check app tests
mypy app

# ML pesado (opcional, no va en la imagen)
pip install -e ".[ml]"

# stack completa
docker compose up -d scraper
```

## Estado

**Fase 1 y 2 cerradas (API + ML)**: 4 de 5 cadenas con scraper, API REST
paginada, ML bajo demanda y job de entrenamiento. 368 tests en verde (90%);
los de integración corren contra Postgres real. `cooperativa_obrera` queda
seedeada sin scraper porque no tiene catálogo público (ver `CONTEXTO.md` §16).
El extra `.[ml]` no se instala en producción; la decisión está en
[ADR-0006](../../docs/adr/0006-dos-modelos-ml.md).
