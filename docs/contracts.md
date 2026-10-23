# Contratos entre servicios — PrecioLocal ER

Especificación de los contratos JSON que comparten los servicios internos y el gateway. Versionado manual hasta el primer release; luego se congelan y se testean con contract tests (ver [testing.md](testing.md)).

## 1. Convenciones generales

- Encoding: UTF-8, JSON estricto.
- Timestamps: ISO 8601 UTC (`2026-03-01T12:00:00Z`).
- Moneda: `ARS` (pesos).
- Errores: RFC 7807 `application/problem+json` con campos `title`, `status`, `detail`, `instance`.

### Casing y tipos: no son uniformes entre servicios

Esto **no** está unificado y hay que respetarlo al integrar:

| | Scraper (Python) | API .NET |
|---|---|---|
| Casing de campos | `snake_case` | `camelCase` |
| Precios | **string** decimal (`"15200.00"`) | **número** (`9144.30`) |
| Fechas de forecast | `date` (`"2026-09-26"`) | `date` (`"2026-09-26"`) |

La regla de "precio como string decimal" (para no perder precisión) **solo se
cumple en el scraper**. .NET devuelve `decimal` serializado como número JSON, que
viaja bien mientras el cliente lo parsee como decimal y no como float.

El motor Java y el gateway usan **camelCase** (`alertId`, `pricePointId`,
`triggeredAt`, `normalizedName`). Las colecciones Mongo `alerts` y
`alert_events` se serializan con ese casing; los endpoints REST de Java también.

Auth actual:

- `X-Admin-Token` protege `POST /scrape/run/{chain}` en el scraper.
- El gateway valida un JWT de Supabase (`Authorization: Bearer ...`) para las
  queries/mutations que lo requieren; `runScrape` además exige rol `admin` o
  `service`.
- `X-Internal-Token` / `X-Correlation-Id` se propagan entre gateway y
  servicios internos. No hay login de usuario final en la UI todavía.


## 2. Entidades canónicas

### Product

```json
{
  "id": 1234,
  "name": "Aceite Girasol 900 cc",
  "normalized_name": "aceite girasol 900cc",
  "brand": "Natura",
  "category": "Aceites",
  "category_slug": "aceites",
  "size_quantity": 0.9,
  "size_unit": "L",
  "ean": "7790070000000"
}
```

### PricePoint

```json
{
  "id": 55678,
  "product_id": 1234,
  "chain": "la_anonima",
  "chain_id": 1,
  "sku_id": 42,
  "store_id": 7,
  "price": "4599.50",
  "promo_type": null,
  "captured_at": "2026-03-01T12:00:00Z",
  "scraped_at": "2026-03-01T12:05:00Z",
  "hash": "a1b2c3d4e5f6..."
}
```

`sku_id` identifica la variante real de venta dentro de una cadena (`product_skus`); puede ser `null` solo para puntos legacy.

### Prediction

Respuesta real de `GET /ml/forecast/{product_id}` (el default es Holt-Winters):

```json
{
  "product_id": 1234,
  "chain": "la_anonima",
  "requested_model": "holtwinters",
  "model": "holtwinters",
  "degraded_reason": null,
  "n_points": 58,
  "mape": null,
  "points": [
    { "date": "2026-09-26", "predicted_price": 9082.64, "conf_low": 8836.35, "conf_high": 9328.92 }
  ]
}
```

- Modelo de producción: `holtwinters`. No devuelve `degraded_reason` en el
  camino normal.
- `model=sarimax` es un pedido explícito que solo corre si el extra `.[ml]`
  está instalado. Si falta (o la serie es corta / no converge), la respuesta
  devuelve `model: "holtwinters"` con `degraded_reason`; `requested_model`
  conserva el pedido. **Leer siempre `model`**, no asumir el pedido.
- `chain` es opcional: si falta, el forecast usa la serie representativa del
  producto (el promedio entre cadenas). Eso no se persiste: la tabla `forecasts`
  exige `chain_id NOT NULL`, así que el job solo guarda series por cadena.
- Nombres de modelo: `holtwinters` y, si el extra está instalado, `sarimax`
  (Python); `mlnet` (.NET).
- `forecast_date` es un `date` (sin hora), no un timestamp.

> **Decisión 2026-10-20 (Opción B):** `statsmodels`, `numpy` y `scikit-learn`
> son el extra opcional `.[ml]` y **no** se instalan en la imagen de producción
> (~200 MB menos). Se aceptan Holt-Winters y robust z-score como los modelos
> reales; SARIMAX/IsolationForest quedan para experimentación local. Ver
> [ADR-0006](adr/0006-dos-modelos-ml.md).

### Alert

> **Persistencia y motor implementados en `analytics-java`.** El motor consume
> `pl:events:price` de Redis, evalúa las alertas activas y escribe
> `alert_events`. **El publicador todavía no está en el scraper**: hoy el canal
> se alimenta con un evento manual en el smoke test. El CRUD de alertas por
> GraphQL (`createAlert`, `toggleAlert`, `deleteAlert`) sigue siendo un stub;
> por ahora las alertas se crean por SQL o seed. El documento canónico es:

```json
{
  "id": 90,
  "user_id": "uuid",
  "product_id": 1234,
  "direction": "below",
  "target_price": "4000.00",
  "active": true,
  "last_triggered_at": null
}
```

`direction`: `below` (avisar cuando baja de X) | `above` (avisar cuando sube de X).

### AlertEvent

> **Implementado.** `AlertEngine` inserta una fila por disparo y
> `GET /api/alert-events?userId=` las devuelve en camelCase. El `id` es
> autogenerado por Postgres.

```json
{
  "id": 412,
  "alert_id": 90,
  "price_point_id": 55678,
  "price": "3990.00",
  "triggered_at": "2026-03-02T10:00:00Z"
}
```

## 3. Endpoints por servicio

Verificado contra el OpenAPI vivo del scraper (`GET :8000/openapi.json`),
`Program.cs`, los controladores de `analytics-java` y el schema de
`services/gateway` el 2026-10-20.

### 3.1 Scraper (Python) — base `/`

| Endpoint | Método | Descripción | Auth |
|---|---|---|---|
| `/health` | GET | Liveness | — |
| `/ready` | GET | Readiness (DB + Redis) | — |
| `/chains` | GET | Listado de cadenas de la tabla `chains` | — |
| `/products?q=&chain=&limit=&offset=` | GET | Búsqueda paginada; devuelve `{products,total}` (una fila por producto y precio vigente por cadena) | — |
| `/products/{product_id}` | GET | Detalle: metadatos + `prices` (último price_point por cadena) | — |
| `/products/{product_id}/history?span=` | GET | Histórico de precios (todos los points por SKU) | — |
| `/categories/{slug}/prices?chain=&limit=` | GET | Ultimo precio vigente por (producto, cadena) de una categoria | - |
| `/ml/forecast/{product_id}?chain=&horizon=&model=` | GET | Forecast Holt-Winters (default) o SARIMAX si el extra `.[ml]` está instalado | — |
| `/ml/anomalies/{product_id}?chain=` | GET | Anomalías del producto (robust z-score; IsolationForest con el extra `.[ml]`) | — |
| `/scrape` | GET | Estado del último scrape por cadena | — |
| `/scrape/run/{chain}` | POST | Ejecuta scrape manual (uno por cadena) | `X-Admin-Token` |
| `/admin/retrain` | POST | Ejecuta el job de entrenamiento en segundo plano | `X-Admin-Token` |

**Paginación de `/products`:** `q` exige 2–80 caracteres; `chain` es un slug
opcional; `limit` va de 1 a 50 (default del backend: 20; la web usa 10) y
`offset` es >= 0. La respuesta es:

```json
{
  "products": [
    {
      "id": 19,
      "name": "Aceite Oliva Orgánico La Anónima x 250 cc.",
      "normalized_name": "aceite oliva organico la anonima 250cc",
      "brand": null,
      "category": null,
      "category_slug": null,
      "size_quantity": "250.000",
      "size_unit": "cc",
      "ean": null,
      "chain": "la_anonima",
      "chain_name": "La Anónima",
      "price": "14050.00",
      "promo_type": "20%_off",
      "captured_at": "2026-10-05T15:48:08.817181Z"
    }
  ],
  "total": 100
}
```

`total` es el número de coincidencias antes de paginar. El orden es por
relevancia (match exacto, precio, recencia y promo) y luego por `id`/`chain` para
que `offset` sea estable. La web refleja `q`, `chain`, `limit` y `offset` en la
URL: el link es la fuente de verdad.

> Las dos rutas de `/scrape` existen **en el scraper**, pero **no se sirven por
> el dominio público**: nginx responde 403 (`docs/security.md` §3.2). Es
>contratado contra `localhost:8000`, nunca contra el puerto de la web.

**Estados de `POST /scrape/run/{chain}`** (contratados, no accidentales):

| Status | Título | Cuándo |
|---|---|---|
| 200 | — | El scrape corrió; el body es el resumen del runner |
| 400 | `Cadena inválida` | La cadena no tiene scraper registrado |
| 401 | `No autorizado` | Token admin ausente o distinto |
| 409 | `En curso` | Ya hay un scrape en curso para esa cadena (advisory lock) |
| 429 | `Demasiadas peticiones` | Se pasó el bucket por IP / por cadena |
| 501 | `No implementado` | `ADMIN_TOKEN` no configurado en el server |
| 503 | `No disponible` | DB no conectada: el lock no se puede tomar |

El **409** viene del lock por cadena (P0.3, `docs/security.md` §3.3). Con los
límites actuales (1 scrape por cadena y hora) no se observa en la práctica
porque el 429 gana la carrera, pero forma parte del contrato para cuando se
suban los límites o las peticiones vengan de IPs distintas: el bucket es por
IP, el lock es por cadena.

**Correcciones respecto de la versión anterior de este doc:**

- `/ml/anomalies` **no** es `?days=`: es `/ml/anomalies/{product_id}` y devuelve
  un objeto `{product_id, chain, n_points, n_anomalies, detector, anomalies}`,
  no una lista plana.
  `scripts/train_forecasts.py` (ver [testing.md](testing.md) §6), no un endpoint.
- `/chains` y `GET /scrape` faltaban en la tabla. `/chains` es además la ruta
  que nginx proxeya a FastAPI: no olvidarla en el regex de `web/docker/nginx.conf`,
  o el browser recibe el `index.html` de Angular en vez de JSON.

### 3.2 .NET API — base `/api`

No expone Swagger ni OpenAPI (no hay endpoint `/swagger`); el mapa de rutas
está en `services/ml-dotnet/Program.cs`.

| Endpoint | Método | Descripción |
|---|---|---|
| `/health` | GET | Liveness. Incluye `mklAvailable` (bool) |
| `/api/forecast/{productId}?chain=&horizon=` | GET | Forecast ML.NET (`SsaForecaster`), modelo `mlnet`. `degradedReason` si cae al fallback |
| `/api/forecast/{productId}/metrics` | GET | Métricas de accuracy del modelo .NET. Acepta `?persist=true` + `chain` y **entonces** escribe en `ml_models`; sin eso devuelve `"persisted": false` |
| `/api/forecast/{productId}/comparison` | GET | Comparativo ADR-0006: métricas de `ml_models` de todos los lenguajes |
| `/api/reports/prices` | GET | Reporte de precios |

**Endpoints que este doc anunciaba y no existen** (devuelven 404, verificado):
`/api/reports/inflation`, `/api/reports/movers`, `/api/reports/category/{slug}`.

`/api/forecast/{id}/comparison` es el que hace útil el ADR-0006: lee de
`ml_models` y devuelve una fila por (modelo, lenguaje). Hay que pedir
`/metrics?persist=true` para que .NET escriba su fila; si no, el comparativo
solo devuelve `language='python'`.

Ejemplo real (producto DEMO, cadena `la_anonima`):

```json
[
  { "model": "holtwinters", "language": "python", "mape": 0.7245, "rmse": 91.5959,  "mae": 66.2720, "numPoints": 10, "trainedAt": "..." },
  { "model": "mlnet",       "language": "dotnet", "mape": 1.2830, "rmse": 149.6189, "mae": 118.1117, "numPoints": 5, "trainedAt": "..." }
]
```

En ese par Holt-Winters (Python) le ganó a SsaForecaster (.NET). Con series
sintéticas cortas y pocos folds no es un resultado concluyente, así que no lo
tomar como veredicto sobre los modelos.

### 3.3 Java API — base `/` + `/api` + STOMP

Implementado en `services/analytics-java` (Spring WebFlux + R2DBC + Mongo
reactivo). El puerto 8080 está bindeado a `127.0.0.1`; el acceso público pasa
por el gateway o por nginx.

| Endpoint | Método | Descripción |
|---|---|---|
| `/health` | GET | Liveness; no toca dependencias |
| `/ready` | GET | Readiness (Postgres + MongoDB) |
| `/api/alert-events?userId=` | GET | Historial de eventos del usuario, camelCase; rate-limited con Resilience4j |
| `/ws` | STOMP/SockJS | Endpoint de WebSocket |

El cliente STOMP usa prefijo de aplicación `/app` y broker simple `/topic`:

- `SEND /app/alerts/subscribe/{userId}` registra la suscripción; los eventos
  llegan a `/topic/alerts/{userId}`.
- `SEND /app/prices/subscribe/{productId}` registra la suscripción; las
  actualizaciones llegan a `/topic/prices/{productId}`.
- `AlertEngine` publica cada disparo en `/topic/alerts/{userId}`.

### 3.4 Gateway GraphQL

Implementado en `services/gateway` (Fastify + Apollo Server 4). Endpoint
`/graphql`; health propio en `/health`. El gateway habla con scraper, .NET y
Java, y valida el JWT de Supabase si viene en `Authorization: Bearer`.

**Queries implementadas:** `health`, `searchProducts(q,chain,limit,offset)`,
`product(id)`, `compare(productIds)`, `priceHistory`, `forecast`,
`forecastComparison`, `anomalies`, `chains`, `alertEvents(limit)`.

> Nota de implementación: el resolver `forecast` usa un DataLoader por
> `productId` y hoy **ignora `chain` y `horizon`** al llamar al scraper
> (`/ml/forecast/{id}` sin query params). El frontend de detalle consume el
> forecast por la ruta REST del scraper, no por este resolver.

**Queries:** `inflationReport` y `topMovers` ahora se calculan en ml-dotnet.
`categoryReport` sigue en stub; `myAlerts` devuelve `[]`.

**Mutations:** `runScrape(chain)` funciona con rol admin/service; `createAlert`,
`toggleAlert`, `deleteAlert` y `retrainModels` devuelven `NOT_IMPLEMENTED` o un
éxito simulado.

**Subscriptions:** `onPriceUpdate` y `onAlertTriggered` existen en el schema,
pero sus resolvers devuelven un async iterator vacío: el front todavía no las
consume. La pieza en tiempo real real es el STOMP de `analytics-java` (§3.3).

## 4. Enumeraciones

### chain

Viven en la tabla `chains` con `slug` único. Datos reales a 2026-09-25:

| slug | Scrapers |
|---|---|
| `la_anonima` | ✅ `services/scraper/app/scrapers/la_anonima.py` |
| `coto_digital` | ✅ `services/scraper/app/scrapers/coto_digital.py` |
| `carrefour` | ✅ `services/scraper/app/scrapers/carrefour.py` |
| `cooperativa_obrera` | ❌ seedeada, sin scraper |
| `changomas` | ✅ `services/scraper/app/scrapers/changomas.py` (MAS Online/VTEX) |

`GET /chains` devuelve las 5 (es una lectura de la tabla, no del scraper).
`POST /scrape/run/{chain}` funciona con las 4 que tienen scraper;
`cooperativa_obrera` sigue sin catálogo público.

### size_unit

`kg` · `L` · `g` · `cc` · `und` (unidad) · `pack`

### period (analytics)

`day` · `week` · `month` — **no implementado**. Era el esquema de las colecciones
de MongoDB del [ADR-0004](adr/0004-mongodb-como-timeseries.md); hoy MongoDB se usa
solo para `alerts`/`alert_events` de `analytics-java`. El histórico de precios
vive en Postgres (`price_points`).
