# Arquitectura — PrecioLocal ER (C4 Nivel 2)

> **Estado real a 2026-10-21:** están implementados **scraper Python (con ML)**,
> **API .NET (con ML.NET)**, **gateway Apollo (GraphQL)**, **motor de alertas
> Java WebFlux (STOMP)** y **Angular PWA**. MongoDB se usa para `alerts`/
> `alert_events`; Redis para cache/lock/pub-sub. La observabilidad sigue parcial:
> `/metrics` apagado por defecto y sin deploy público.

## 1. C4 Nivel 1 (Contexto)

```
[Usuario web/móvil] --HTTPS--> [Sistema PrecioLocal ER]
                                  │
                                  ▼
                          [Supermercados de Entre Ríos]
                              (fuente externa, scraping)
```

## 2. C4 Nivel 2 (Contenedores)

```
┌─────────────────────────────────────────────────────────────────┐
│  Angular PWA (Netlify)  ·  Signals  ·  fetch HTTP directo        │
│  Buscador · Comparador · Detalle con histórico · Predicciones    │
│  REST (scraper/.NET) + GraphQL (/graphql) + STOMP (/ws)          │
└────────────────────────────┬────────────────────────────────────┘
                             │ REST vía nginx (:4200 → :8000/:8001)
                             ▼
        ┌────────────────────────┴─────────────────────────┐
        │                                                  │
┌───────▼──────────────────────┐              ┌────────────▼─────────────┐
│ Scraper + ML  ✅ existe      │              │ API .NET 8  ✅ existe     │
│ Python 3.12 · FastAPI        │              │ SsaForecaster (ML.NET)   │
│ httpx + BeautifulSoup        │              │ EF Core                  │
│ Holt-Winters / SARIMAX       │              │ ReportService (LINQ)     │
│ anomalías (IForest/zscore)   │              │ ForecastService          │
└───────┬──────────────────────┘              └────────────┬─────────────┘
        │                                                  │
        ▼                                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│ PostgreSQL  ✅ existe — transaccional, fuente de verdad           │
│ price_points · product_skus · forecasts · ml_models · anomalies │
└─────────────────────────────────────────────────────────────────┘
        ▲                                                  ▲
        │ locks de scraping                                │ (EF Core)
┌───────┴──────────────┐
│ Redis  ✅ existe      │  storage del rate limiter + caché
└──────────────────────┘

✅ Gateway Apollo (GraphQL + DataLoader), motor de alertas Java WebFlux
   (STOMP `/ws`) y MongoDB para `alerts`/`alert_events` ya existen. La
   observabilidad (OTel/Prometheus/Grafana) sigue parcial: `/metrics` está
   apagado por defecto.
```

> **Actualización 2026-10-20:** el diagrama conserva la topología REST de Fase 2.
> El frontend también usa GraphQL (`/graphql` → gateway) y STOMP (`/ws` →
> analytics-java). El detalle de rutas está en
> [contracts.md](contracts.md) §3.4.

**Nota de routing:** nginx (`web/docker/nginx.conf`) reparte `/products*`,
`/chains`, `/ml*`, `/ready` al scraper, `/api*` a .NET y `/graphql` + `/ws` al
gateway. El regex del `location /` tiene que incluir cada prefijo a mano;
olvidarse de `/chains` hizo que el browser recibiera el `index.html` en vez de
JSON.

## 3. Rol único por pieza

| Pieza | Estado | Rol único | Lo que NO hace |
|---|---|---|---|
| Python + FastAPI | ✅ | Scraping, normalización, ML estadístico (Holt-Winters + robust z-score; SARIMAX/IsolationForest con extra `[ml]`) | No sirve reportes agregados ni alertas |
| .NET 8 + ML.NET | ✅ | SsaForecaster + reportes LINQ | No scrapea, no alerta |
| Angular PWA | ✅ | UI, detalle, comparador, gráficos de forecast | No lógica de negocio |
| Java WebFlux | ✅ local | Motor de alertas reactivo + WebSocket STOMP | No entrena modelos, no persiste precios crudos |
| Node Apollo | ✅ local | GraphQL, DataLoader; subscriptions aún stub | No tiene lógica de negocio |
| PostgreSQL | ✅ | Transaccional: precios, productos, forecasts, anomalías, alertas | No guarda analytics agregados |
| MongoDB | ✅ | Alertas y eventos (`alerts`, `alert_events`) | No es fuente de verdad |
| Grafana Cloud | 🟡 | Observabilidad | `/metrics` apagado por defecto; falta verificación |

**Prueba de fuego:** si quitas una pieza, algo deja de funcionar y no hay sustituto trivial.
Hoy se cumple para scraper, .NET, Angular y Postgres. Para Redis también: sin
él el scraper sigue andando, solo pierde el rate limit compartido entre
procesos. MongoDB **no** cumple la prueba todavía: quitarlo mañana no rompe
nada, porque todavía nadie lo consulta.

## 4. Flujo de datos (happy path real)

1. **Scraper** captura precios por cadena (`POST /scrape/run/{chain}`, o el job
   programado) → normaliza unidades/marcas (`app/pipelines/normalize.py`) →
   dedupe por hash (`app/pipelines/dedupe.py`) → upsert de `products` y
   `product_skus` → `INSERT ... ON CONFLICT (hash) DO NOTHING` en `price_points`.
2. **Entrenamiento (job, no endpoint):** `scripts/train_forecasts.py` recorre los
   pares (producto, cadena) con ≥ 14 días, entrena, y escribe en `forecasts`
   (7 días por par), `anomalies` (outliers) y `ml_models` (métricas MAPE/RMSE/MAE
   del walk-forward con `language='python'`). Todo idempotente con
   `ON CONFLICT DO UPDATE`.
3. **.NET** calcula SsaForecaster bajo demanda por request
   (`GET /api/forecast/{id}`) y expone el comparativo del ADR-0006 en
   `GET /api/forecast/{id}/comparison`, que lee `ml_models` y devuelve una fila
   por (modelo, lenguaje). Sus propias métricas se persisten **opt-in**, vía
   `GET /api/forecast/{id}/metrics?chain=<slug>&persist=true`; sin ese `persist`
   la respuesta trae `"persisted": false` y la tabla solo tiene filas de Python.
4. **Angular** pide REST al proxy de nginx (búsqueda, detalle, forecast) y
   GraphQL al gateway (dashboard, comparador, alertas); las alertas en vivo
   llegan por STOMP.

🟡 **Parcial:** `analytics-java` ya consume `pl:events:price`, evalúa alertas,
   escribe `alert_events` y publica en `pl:events:alert` y por STOMP. Lo que
   falta es que el scraper **publique** `pl:events:price` al guardar precios:
   hoy el smoke test inyecta el evento manualmente. `LISTEN/NOTIFY` sigue sin
   implementar.

## 5. Decisión clave: LISTEN/NOTIFY + pub/sub

🟡 **Parcial: Redis implementado; `LISTEN/NOTIFY` no.** La doble vía del
ADR-0003/0005 para evitar punto único:

- **Redis pub/sub** (`pl:events:price`, `pl:events:alert`): implementado en el
  motor Java (consumo, evaluación y publicación). Falta el publicador del
  scraper para cerrar el circuito automático.
- **Postgres `LISTEN/NOTIFY`**: respaldo determinista si Redis no está
  disponible; decidido pero sin código.

De [ADR-0003](adr/0003-webflux-para-alertas.md) y [ADR-0005](adr/0005-redis-cache-lock-pubsub.md).

## 6. Vistas de datos por servicio

| Servicio | Lee | Escribe | Estado |
|---|---|---|---|
| Python scraper | Redis (rate limit) | `products`, `product_skus`, `price_points` | ✅ |
| Job `train_forecasts.py` | `price_points` | `forecasts`, `anomalies`, `ml_models` | ✅ |
| .NET | `price_points`, `products`, `ml_models` | `ml_models`, solo con `?persist=true` | ✅ |
| Java | `alerts`, `alert_events`, pub/sub | `alert_events` | 🟡 local (falta publicador del scraper) |
| Gateway | 4 servicios vía REST | `alerts` (mutación en stub) | 🟡 local |

## 7. Diagrama de despliegue (free tiers)

Lo mismo que [deploy.md](deploy.md) §1: scraper y .NET en Render, Angular en
Netlify, Postgres en Supabase, Redis en Upstash. **Todos los servicios existen
en local**; el deploy sigue pendiente de cuentas y secretos: Java en Railway,
gateway en Render, MongoDB en Atlas M0, Grafana Cloud.

Riesgo de cold start en Render documentado en [ADR-0007](adr/0007-render-cold-start-consciente.md).

## 8. Enlaces relacionados

- Contratos entre servicios: [contracts.md](contracts.md)
- Seguridad (OWASP): [security.md](security.md)
- Estrategia de testing: [testing.md](testing.md)
- Decisiones: [adr/](adr/)