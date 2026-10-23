# Estrategia de testing — PrecioLocal ER

> **Objetivo declarado:** 75% de cobertura en lógica de negocio, 0% en boilerplate.
> Cobertura actual del scraper Python: **90%** (`368 tests`: 205 unit + 163 integración).

## 1. Pirámide por capa

Estado real a 2026-10-20. La columna "existencia" marca si la capa está
construida o es plan (`Fase N`).

| Capa | Qué se prueba | Cómo | Dónde | Existencia |
|---|---|---|---|---|
| Unit | Parsers, normalización, features ML, dedupe, state, robots | Sin I/O real; fixtures en memoria | `services/scraper/tests/unit` | ✅ 205 tests |
| Unit | `SsaForecastService` | xUnit, serie sintética | `services/ml-dotnet/tests` | ✅ 13 tests |
| Unit | Servicios Angular + modelos | Karma + Jasmine + ChromeHeadless | `web/src/**/*.spec.ts` | ✅ 35 tests (5 specs) |
| Integration | Endpoints + DB real | `pytest` contra Postgres de `docker compose` | `services/scraper/tests/integration` | ✅ 163 tests |
| Contract | Schema JSON contra este doc | — | `services/scraper/tests/integration/test_contracts.py` | ✅ 35 tests |
| E2E | Recorrido completo con la UI | Playwright contra la topología real | `web/e2e/` | ✅ 29 tests |
| Smoke | API sin browser | `httpx.ASGITransport` in-process | `scripts/smoke_e2e.py` | ✅ 1 script |

**Lo que NO existe todavía** (y este doc antes lo daba por hecho):

- ⚠️ **Testcontainers** — Java lo usa para integración; Python sigue usando el
  Postgres de `docker compose` / el service container de CI.
- ❌ **Jest / `@testing-library/angular`** — Angular usa **Karma + Jasmine**
  (ver `web/package.json`), configurado con `ng test --browsers=ChromeHeadless`.
- ⚠️ **E2E del flujo de alertas** — la UI existe, pero el CRUD GraphQL está en
  stub y no hay cobertura end-to-end de alertas.

## 2. Matriz por servicio

| Servicio | Unit | Integration | Contract | E2E | Estado |
|---|---|---|---|---|---|
| Python scraper (`services/scraper`) | Parsers con fixtures HTML, pipelines, ML, storage, robots (205) | 163 tests con Postgres real | 35 tests contra `docs/contracts.md` | `smoke_e2e.py` + Playwright | 🟢 Fase 1–2 |
| .NET ML (`services/ml-dotnet`) | `SsaForecastService` (13 tests) | — | — | vía Playwright (comparativa de modelos) | 🟢 Fase 2 |
| Angular (`web`) | 5 specs, 35 tests | — | — | 29 tests de Playwright | 🟢 Fase 5 (4/6) |
| Gateway GraphQL | 4 tests | 5 tests | — | 8 tests de proxy (Playwright) | 🟢 Fase 4 |
| Java analytics | 6 tests | 8 tests (Testcontainers) | — | — | 🟢 Fase 3 local |

**Regla por lenguaje (lo que está en uso):**

- Python: `pytest`, `pytest-asyncio`, `respx` (mock httpx), `pytest-cov`.
  Config en `services/scraper/pyproject.toml` (`[tool.pytest.ini_options]`).
- .NET: xUnit. Ver `services/ml-dotnet/tests/PrecioLocal.Ml.Tests.csproj`.
- Angular: Karma + Jasmine + `karma-chrome-launcher`, vía `ng test`.
- E2E de browser: Playwright (`@playwright/test` en `web/package.json`),
  TypeScript, Chromium.

## 3. E2E de browser (Playwright) — `web/e2e/`

**Corre contra la topología real**, no contra un server mock: el `baseURL` es
`http://127.0.0.1:4200` (el nginx del compose), que proxea `/chains`,
`/products*`, `/ml/*` y `/api/*` a `scraper:8000` y `ml-dotnet:8001`.

Un mock habría repetido el error de `smoke_e2e.py`: no verifica ni el proxy ni
los headers de seguridad, que es donde estaban los bugs (ver `CONTEXTO.md` §17
— el E2E encontró 4: el gráfico roto, el service worker que mataba la API en la
segunda visita, los estilos ausentes por CSP, y el filtro de cadena que no se
sacaba de la URL).

| Spec | Qué cubre |
|---|---|
| `busqueda.spec.ts` | búsqueda con debounce, mínimo de caracteres, resultado → detalle, back operable, ruta desconocida, chrome de la app |
| `cadenas.spec.ts` | "Precio por cadena" del detalle, filtro por cadena, el precio del detalle cambia con la cadena, quitar el filtro saca el `?chain=` de la URL, el catálogo de chips viene del API |
| `prediccion.spec.ts` | banda de 7 días ordenada, coincidencia o no de los dos modelos, degradaciones reportadas por el API, el gráfico dibuja y cambia con el período, producto inexistente, cero errores de JS y cero requests fallidas |
| `proxy-contrato.spec.ts` | **guard de config**: todo endpoint que llama el front está proxieado en nginx y en `proxy.conf.json`, con el orden de precedencia de nginx; las rutas de la SPA caen en `index.html`; los dotfiles se niegan; la CSP no permite ni un dedo más de lo que la app usa |
| `proxy-contrato.spec.ts` (frontera de confianza) | **guard de la frontera de confianza del proxy**: nginx sobreescribe `X-Forwarded-For` en vez de appendearlo; la allow-list es una sola IP y los tres puntos que la consumen (nginx, uvicorn, Kestrel) toman la misma variable; `POST /scrape/run` responde **403 de verdad** en el dominio público, no `index.html` con 200 |

### 3.1 El fixture se descubre, no se hardcodea

`e2e/global-setup.ts` consulta la API real y busca un producto con **≥2 cadenas**
y forecast de **los dos motores** (Python y .NET); escribe
`e2e/.data/fixture.json`. Si no encuentra uno, falla con un mensaje que explica
que hay que correr `make e2e-data`.

**Por qué hace falta `demo` + `train`:** los productos scrapeados son de una
sola cadena cada uno (el modelo de `chains` lo impone), así que no existe ningún
producto real comparable entre cadenas — una revisión de 23 términos no encontró
ninguno. Los comparables son los `DEMO-*` que crea `seed_demo_history.py`.
Sin ese paso, los specs de cadenas correrían contra el caso vacío.

### 3.2 Variables de entorno

| Variable | Default | Para qué |
|---|---|---|
| `E2E_BASE_URL` | `http://127.0.0.1:4200` | contra qué stack se corre |
| `E2E_QUERY` | `aceite` | término de búsqueda |
| `E2E_FIXTURE_QUERY` | `DEMO` | qué productos se buscan para el discovery |

### 3.3 ⚠️ El E2E necesita el rate limiter del scraper subido

El scraper limita a **30 requests/minuto por IP** (slowapi, OWASP A04). Todo el
tráfico del E2E —el del browser y el del discovery— entra por nginx, así que
**comparte una sola IP de origen**: 29 tests × 5-10 requests cada uno barren el
presupuesto en ~14 segundos y la suite se muere de `429` en `~/global-setup` (o a
mitad de un test, que es peor: verde en una corrida, rojo en otra).

Por eso `.env.example` trae la variable **comentada**:

```bash
echo 'RATE_LIMIT_REQUESTS_PER_MINUTE=1000' >> .env
docker compose up -d scraper      # el compose la pasa al contenedor
```

**El default de la app sigue siendo 30/min**: subirlo es una decisión del
entorno de test, no un cambio de seguridad. Después del E2E hay que **quitar la
línea de `.env` y recrear `scraper` + `web`** para volver al límite real. El job
`web-e2e` de CI lo hace en su propio `.env` efímero.

### 3.3.1 ⚠️ Si recreás el scraper, recreá también la web

Este error costó tiempo y no se parece a nada que uno haya visto antes:

```
$ curl -o /dev/null -w '%{http_code}\n' http://localhost:8000/chains
200                                  # el scraper está sano
$ curl -o /dev/null -w '%{http_code}\n' http://localhost:4200/chains
502                                  # pero el proxy devuelve 502
```

**Causa:** nginx resuelve el nombre del upstream (`proxy_pass
http://scraper:8000`) **una sola vez, al cargar la configuración**. No usa el
DNS de Docker en cada request. Si el contenedor del scraper se recrea y Docker
le asigna otra IP, nginx sigue hablando con la IP vieja y todos los endpoints del
API dan 502 — con un servicio sano detrás.

**No lo arregla** reiniciar solo el scraper, ni `docker compose restart`. Hay que
recrear el que tiene el proxy:

```bash
docker compose up -d --force-recreate web
```

**Cómo se distingue de un problema real:** si `wget` de adentro del contenedor web
al upstream funciona pero por el proxy da 502, es esto. Un servicio caído da
`Connection refused` en los dos caminos.

Es la segunda vez que un "recreate de un contenedor" rompe algo que no tiene
nada que ver (la otra fue el `PID 1` del scraper, `CONTEXTO.md` §18.4). Con
Docker, recrear un servicio invalida las suposiciones de los demás.

### 3.4 Smoke de API (sigue existiendo, y sigue siendo otra cosa)

`scripts/smoke_e2e.py` monta la app **in-process** con
`httpx.ASGITransport` (no necesita servidor ni pasa por nginx) y cubre:

1. `GET /ready` — chequeo de dependencias.
2. `POST /scrape/run/{chain}` — **scrape real** contra los sitios de cada cadena.
   Ojo: pega contra la web externa, así que no es un test offline.
3. `GET /products?q=aceite` — búsqueda.
4. `GET /products/{id}` — detalle con los precios de todas las cadenas.
5. `GET /products/{id}/history?span=30d` — serie histórica.

Lo que el smoke **no** cubre y el E2E de browser **sí**:

- ❌ El proxy de nginx (`:4200`). Por eso el bug de `/chains` —que devolvía
  el `index.html` de Angular en vez de JSON— no lo detectaba: el smoke
  llamaba a la app directo y se salteaba la capa de routing.
- ❌ Los endpoints de ML (`/ml/forecast/{id}`, `/api/forecast/{id}`) vía el proxy.
- ❌ La UI: DOM, estilos, click, canvas.

No hay servicio de alertas, así que el flujo de alertas no es testeable aún.

## 4. Fixtures y data de prueba

- `services/scraper/tests/fixtures/`: HTML **real** de cada cadena (capturado y versionado).
- Los fixtures se actualizan con una PR aparte cuando el sitio cambia (el HTML es un contrato externo).
- **Data sintética de demo:** `scripts/seed_demo_history.py`, que crea productos
  `DEMO-*` (prefijo para distinguirlos de un vistazo) y se borran con `--purge`.
  Aborta si `APP_ENVIRONMENT=production` o si la base no es local.
  Es también la data que consume el E2E de browser (§3.1).
- No hay `db/mongodb/seed_demo.js`: MongoDB todavía no se usa (ver
  [ADR-0004](adr/0004-mongodb-como-timeseries.md)).

## 5. Métricas

- Cobertura del scraper medida en cada `pytest` (`--cov=app`, ver
  `addopts` en `pyproject.toml`), subida a Codecov desde `scraper-test`.
- El badge de Codecov del README tiene backing desde el workflow de CI.
- Meta: >75% lógica de negocio; boilerplate/config excluido.

## 6. Comandos por servicio

```bash
# Python scraper (desde services/scraper)
pytest -q                       # 368 tests (163 de integración) + cobertura
ruff check app tests            # lo que corre CI
mypy app                        # lo que corre CI

# .NET ML (desde services/ml-dotnet)
dotnet test -c Release          # 13 tests
# requiere el runtime de Intel OpenMP: sudo ./scripts/install-mkl-runtime.sh

# Angular (desde web)
npm ci
npx ng test --watch=false --browsers=ChromeHeadless   # 35 tests, Chrome real
npm run build

# E2E de browser (desde web, con la stack arriba y datos DEMO)
npx playwright install chromium     # una sola vez
npm run e2e                         # 29 tests contra http://127.0.0.1:4200
npm run e2e -- --ui                 # Playwright UI mode
npm run e2e:headed                  # con browser visible
npm run e2e:report                  # abre el reporte HTML

# Igual, desde la raíz (WSL / Linux / git-bash)
./scripts/pl.sh e2e-install
./scripts/pl.sh up
./scripts/pl.sh e2e-data            # demo + train (idempotente)
./scripts/pl.sh e2e

# E2E local: stack levantada + DATABASE_URL
python scripts/smoke_e2e.py

# Datos de demo + entrenamiento
python scripts/seed_demo_history.py     # 60 días DEMO-* 
python scripts/train_forecasts.py       # persiste forecasts + anomalías + métricas
```

## 7. Pendiente

- E2E del flujo de alertas: no hay UI (`PLAN.md` §5).
- La búsqueda no sobrevive la navegación (al volver del detalle, la caja queda
  vacía). El E2E verifica que volver sea operable, no que la consulta persista.
- El job `web-e2e` de CI está escrito pero **nunca corrió en GitHub** (el repo
  no tiene push): el verde local no es evidencia de que el pipeline funcione.

## 7b. Base de test para la integración con Postgres

Los 163 tests de integración necesitan una base propia: `conftest.py` **trunca
tablas**, así que si `TEST_DATABASE_URL` apunta a `preciolocal` (o `postgres`) la
suite falla antes de empezar.

La primera vez, en el Postgres del compose:

```sql
CREATE ROLE preciolocal_test WITH LOGIN CREATEDB PASSWORD 'test-password-not-a-secret';
CREATE DATABASE preciolocal_test OWNER preciolocal_test;
```

El `OWNER` importa: el `conftest` deriva la URL de admin reemplazando el path de
`TEST_DATABASE_URL`, así que se conecta como `preciolocal_test` y aplica las
migraciones. Con la base creada por otro rol (por ejemplo, una `CREATE DATABASE`
de la imagen oficial), los tests dan `InsufficientPrivilegeError` en vez de
fallar claro.

En CI esto lo hace el job `scraper-tests`; localmente hay que hacerlo a mano una
vez. Si no se setea la variable, los tests de integración hacen `skip` en
silencio y la cobertura parece completa — por eso el job falla si algún test
quedó skippeado.

### 7c. Privilegios que el rol de test necesita (P0.1)

`tests/integration/test_service_roles.py` crea roles de cluster efímeros y les
pone membresías a los grupos de servicio, así que `preciolocal_test` necesita
más que `CREATEROLE`:

```sql
-- 1. La base (una sola vez)
CREATE ROLE preciolocal_test WITH LOGIN CREATEDB CREATEROLE PASSWORD 'test';
CREATE DATABASE preciolocal_test OWNER preciolocal_test;

-- 2. ADMIN OPTION sobre los grupos de servicio (una sola vez, por migración)
GRANT ADMIN OPTION ON ROLE pl_scraper, pl_app, pl_ml TO preciolocal_test;
```

El paso 2 no es opcional ni un oversight: sin ADMIN OPTION, `GRANT pl_scraper TO
<rol-efímero>` falla con `permission denied to grant role`, y el test de RLS no
puede armarse. En CI no hace falta nada: el job corre como superusuario del
`postgres:16-alpine`.

El `ADMIN OPTION` queda en el cluster y por eso el teardown del fixture tiene
que **revocar la membresía antes de dropear** el rol: desde Postgres 16, un rol
con `CREATEROLE` recibe automáticamente ADMIN OPTION sobre los roles que crea, así
que el propio `preciolocal_test` queda como miembro del rol efímero y el
`DROP ROLE` falla con `DependentObjectsStillExistError` si no se revoca antes.

> El cleanup de ese fixture **afirma** que el rol desapareció y que no quedó
> ningún `pl_t_*` vivo en el cluster. No es un detalle del test: una versión
> anterior se tragaba los `PostgresError` con un `continue`, y por eso dejó 12
> roles huérfanos por corrida en el cluster de desarrollo sin que ningún test se
> enterara — el `DROP` fallaba, el error se descartaba y la suite pasaba en
> verde. Para limpiar huérfanos de corridas anteriores:
>
> ```sql
> -- como superusuario del contenedor
> SELECT rolname FROM pg_roles WHERE rolname LIKE 'pl\_t\_%';
> REVOKE pl_t_... FROM preciolocal_test;   -- uno por rol huérfano
> DROP ROLE pl_t_...;
> ```

### 7d. Tests del lock por cadena (P0.3)

`services/scraper/tests/integration/test_chain_lock.py` prueba contra Postgres
real lo que los fakes no pueden probar: que `pg_try_advisory_lock` es de
**sesión**. Cubre que el lock sobrevive a un commit intermedio (un runner real
hace cientos), que un unlock desde otra sesión no libera nada, que muere con la
conexión (el argumento para preferir Postgres a un lock en memoria), que es por
cadena y no global, y que se libera en el camino normal.

`services/scraper/tests/unit/test_scrape_guards.py` cubre la parte de la app con
fakes: 409 cuando el lock está tomado, que no se intente liberar un lock que no
se tomó, que el unlock corra aunque el scrape explote (`try/finally`), fail-closed
sin DB, y que el token no llegue al log.

> Para **ver el 409 de verdad** contra el contenedor hay que subir el límite por
> cadena, porque `docker-compose.yml` no reenvía `SCRAPE_LIMIT_PER_CHAIN_HOUR`
> al `environment` del scraper y el contenedor corre siempre con el default del
> código (1/hora): con ese default el 429 responde antes y el lock nunca se ve.
> Agregando esas dos variables al `environment` del scraper, tres `POST`
> simultáneos dan `1×200 + 2×409 En curso`.
