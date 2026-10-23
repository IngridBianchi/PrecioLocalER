-- ============================================================
-- PrecioLocal ER — 010_product_skus_soft_delete.sql
-- Soft-delete de SKU (deuda técnica T8, auditada 2026-10-01) y
-- cierre de un hueco de privilegios detectado en la misma auditoría.
--
-- 1. `product_skus.active` existía desde 006 pero nunca se
--    apagaba: un SKU que la cadena sacaba de góndela seguía
--    figurando como precio vigente para siempre, porque
--    search_products / product_detail filtran por `s.active`.
--    El barrido lo hace ScrapeRunner tras un scrape limpio de la
--    cadena (deactivate_missing_skus).
--
-- 2. Privilegios: 007 corre DESPUÉS de 006 y hace
--    `REVOKE ALL ON ALL TABLES ... FROM pl_scraper, pl_app`
--    seguido de una lista explícita de GRANTs que se olvidó de
--    product_skus. Hoy no rompe nada porque la app conecta como
--    dueño de los objetos (POSTGRES_USER=preciolocal), no como
--    pl_scraper; pero el día que se use el rol de servicio, el
--    scraper se queda sin permisos sobre la tabla que más escribe.
--    Mismo hueco conceptual en las tablas de 008, que 007 nunca
--    vio, pero 008 ya se encarga de otorgarlas explícitamente.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1. Índice parcial para el barrido de SKU dados de baja.
--    El UPDATE es `WHERE chain_id = $1 AND active`, así que un
--    índice parcial sobre los activos lo vuelve barato.
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_product_skus_chain_active
    ON product_skus (chain_id)
    WHERE active;

-- Listado de "SKU que la cadena aún no vio en este scrape".
CREATE INDEX IF NOT EXISTS idx_product_skus_chain_last_seen
    ON product_skus (chain_id, last_seen_at DESC);

-- ------------------------------------------------------------
-- 2. Privilegios del rol de servicio sobre product_skus
--    (mismo criterio que 007/008: el scraper escribe, la app lee).
-- ------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON product_skus TO pl_scraper;
GRANT SELECT ON product_skus TO pl_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pl_scraper, pl_app;

COMMIT;