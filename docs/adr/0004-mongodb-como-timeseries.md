# ADR-0004: MongoDB como time-series de analytics

## Estado

Aceptado · 2026-09-23

> **Implementación al 2026-10-20:** MongoDB está en uso por `analytics-java`,
> pero guarda `alerts` y `alert_events`, no la colección `price_analytics` que
> describe esta decisión. Los reportes de inflación/movers siguen en stub y el
> histórico crudo vive en Postgres. Esta ADR conserva el diseño objetivo; la
> parte de agregación time-series **no está implementada**.

## Contexto

Los reportes del dashboard (inflación por categoría, p50/p90, tablero realtime) agregan millones de `price_points` por período. Hacer agregaciones OLAP pesadas sobre la tabla transaccional de Postgres en cada render degrada la fuente de verdad y complica el acceso concurrente.

## Decisión

**MongoDB Atlas (M0)** como **capa de analytics independiente**, no como fuente de verdad:

- Colección `price_analytics`: documentos `{ product_id, period, bucket_ts, avg, min, max, p50, p90, count }` con unique index `{product_id, period, bucket_ts}`.
- El `AlertEngine` de Java (reactivo, ver ADR-0003) agrega en streaming cada nuevo precio → upsert atómico por bucket de día/semana/mes.
- Los reportes leen de la colección ya agregada (rápido, sin escanear precios brutos).
- **TTL index (48h)** en `price_board` para el tablero realtime; los aggregates históricos se conservan por decisión de producto.
- Se usa el driver **reactivo** y el pipeline de aggregation de Mongo (match → group) — no ORMs.

### Qué NO hace

Mongo no guarda precios crudos ni reemplaza el transaccional: lo crudo vive en Postgres, que es lo que rellena tanto el scraper como el retraining de los MLs.

## Alternativas consideradas

| Alternativa | Pros | Contras | Por qué no |
|---|---|---|---|
| TimescaleDB (Postgres + extensión) | Un solo motor | No disponible como free tier gestionado sin operar uno mismo | Free tier con Supabase no lo trae |
| ClickHouse / InfluxDB | Analítica de series de tiempo de élite | Sin managed free tier serio; curva de operación alta | Overkill |
| Agregar en Postgres con vistas materializadas | Simple | Tierra de latencia en volúmenes grandes; competir por la fuente de verdad | Option de fallback (ver migración 004) |

## Consecuencias

- **Positivas:** reportes rápidos independientes del transaccional; demuestra un uso **tipado y justificado** de NoSQL (no "codecapricho"); demuestra aggregation pipeline + TTL.
- **Negativas:** latencia de eventual consistency (aceptable, son reportes); un tercer motor que el operador debe vigilar.
- **Neutras:** la fuente de verdad sigue siendo Postgres → el modelo queda defendible.

## Referencias

- [ADR-0003-webflux-para-alertas](0003-webflux-para-alertas.md)
- [db/mongodb/indexes.js](../../db/mongodb/indexes.js) — índices/agregación