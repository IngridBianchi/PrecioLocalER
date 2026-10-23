-- ============================================================
-- PrecioLocal ER — 009_schema_migrations.sql
-- Libro de migraciones (ledger).
--
-- Problema que resuelve (deuda técnica T4, auditada 2026-10-01):
-- hasta 008 no había forma de saber qué versión tenía aplicada una
-- base. `make db-migrate` reproducía los .sql en orden y reliedía
-- en que cada archivo fuera idempotente (IF NOT EXISTS /
-- ON CONFLICT), lo cual funciona pero es opaco: si un .sql estaba
-- roto a mitad de camino no había forma de detectarlo.
--
-- `schema_migrations` convierte eso en un ledger explícito:
-- `make db-migrate` saltea lo ya registrado y `make db-status`
-- muestra el estado. El backfill marca 001-008 como aplicadas
-- porque, por numeración, para que este archivo corra ya se
-- aplicaron todas.
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- Backfill: 001-008 ya están en la base en cualquier escenario en el
-- que corra 009. ON CONFLICT para que sea seguro reaplicar.
INSERT INTO schema_migrations (version)
VALUES ('001_init'),
       ('002_seed'),
       ('003_indexes_triggers'),
       ('004_products_normalized_unique'),
       ('005_add_coto_carrefour'),
       ('006_add_product_skus'),
       ('007_rls_revoke'),
       ('008_ml')
ON CONFLICT (version) DO NOTHING;

COMMENT ON TABLE schema_migrations IS
    'Ledger de migraciones aplicadas. Una fila por archivo de db/supabase/migrations/.';

COMMIT;