# db — Esquemas y datos

## Estructura real

```
supabase/
  migrations/       # SQL versionado en orden NNN_*.sql
```

**No hay `db/mongodb/`.** MongoDB está en `docker-compose.yml` (puerto 27018) y
lo consume `analytics-java` para `alerts` y `alert_events`. La colección
time-series `price_analytics` del
[ADR-0004](../docs/adr/0004-mongodb-como-timeseries.md) **no está implementada**.
Todo el histórico de precios vive en Postgres, en `price_points`.

## Migraciones

Secuencia completa y sin huecos, en orden estricto:

| Archivo | Qué hace |
|---|---|
| `001_init.sql` | Schema base: `chains`, `stores`, `categories`, `brands`, `products`, `price_points`, `predictions`, `users`, `alerts`, `alert_events` |
| `002_seed.sql` | Cadenas reales de Entre Ríos + jerarquía de categorías (2 niveles) + marcas de góndola |
| `003_indexes_triggers.sql` | Índices operativos + trigger de `NOTIFY` (`LISTEN/NOTIFY`) para el AlertEngine Java (ADR-0003) |
| `004_products_normalized_unique.sql` | Unique index en `products.normalized_name`, que es la llave del `ON CONFLICT` del upsert |
| `005_add_coto_carrefour.sql` | Incorpora Coto Digital y Carrefour al seed de cadenas |
| `006_add_product_skus.sql` | `product_skus` (variante de venta real por cadena) + precio representativo |
| `007_rls_revoke.sql` | Endurecimiento de la base (WP1, ver [docs/security.md](../docs/security.md)) |
| `008_ml.sql` | `forecasts`, `ml_models`, `anomalies` (ADR-0006) |
| `009_schema_migrations.sql` | Ledger de migraciones aplicadas |
| `010_product_skus_soft_delete.sql` | Soft-delete de variantes por cadena |
| `011_service_roles.sql` | Grupos `pl_scraper`/`pl_app`/`pl_ml` y sus policies |
| `012_analytics_service_role.sql` | Grupo `pl_analytics` y policies de servicio para alertas |

Ojo con `003`: el trigger de `NOTIFY` **ya está en la base**, pero el
`AlertEngine` consume Redis (`pl:events:price`), no este canal; las
notificaciones de Postgres siguen sin consumidor.


### Tablas de ML (`008_ml.sql`)

- `forecasts` — un punto por día futuro por `(product_id, chain_id, model,
  forecast_date)`. **`chain_id` es NOT NULL**: por eso el job solo persiste
  series con cadena resuelta, no la serie de mercado.
- `ml_models` — métricas (MAPE/RMSE/MAE) por `(model, language, product_id,
  chain_id)`, con `language` en `('python','dotnet')`. Es la tabla que hace
  posible el comparativo del ADR-0006.
- `anomalies` — outliers, únicos por `(product_id, chain_id, captured_at)`.

### Tablas que existen pero están vacías

`alerts`, `alert_events`, `predictions` (y `users`, salvo seeds de prueba). El
motor Java existe y se probó con un evento Redis manual; falta que el scraper
publique `pl:events:price`. `predictions` quedó reemplazada por `forecasts`.

## Reglas

- La **fuente de verdad** es PostgreSQL. MongoDB guarda alertas/eventos; la
  agregación time-series del ADR-0004 no está implementada.
- Todas las migraciones son idempotentes (`IF NOT EXISTS` / `ON CONFLICT`).
- El contrato de datos canónico vive en [docs/contracts.md](../docs/contracts.md).

## Datos de demo

Los productos sintéticos se generan con `scripts/seed_demo_history.py`, no con
un seed de Mongo. Se llaman `DEMO-*` y se borran con:

```bash
python scripts/seed_demo_history.py --purge
```
