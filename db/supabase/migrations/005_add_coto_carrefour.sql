-- ============================================================
-- PrecioLocal ER — 005_add_coto_carrefour.sql
-- Fase 2: se incorporan Coto Digital y Carrefour al scraper
-- multi-cadena. Idempotente (slugs únicos como llave natural).
-- ============================================================

BEGIN;

INSERT INTO chains (name, slug, domain, region, active, scrape_schedule)
VALUES
    ('Coto Digital',  'coto_digital', 'cotodigital.com.ar', 'entre_rios', true, '0 10 * * 1'),
    ('Carrefour',     'carrefour',    'carrefour.com.ar',   'entre_rios', true, '0 11 * * 1')
ON CONFLICT (slug) DO NOTHING;

COMMIT;