-- ============================================================
-- PrecioLocal ER — 004_products_normalized_unique.sql
-- Unique index sobre products.normalized_name.
-- El scraper usa ON CONFLICT (normalized_name) para la upsert
-- idempotente y el dedupe de productos (ver services/scraper).
-- ============================================================

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS idx_products_normalized_unique
    ON products (normalized_name);

COMMIT;