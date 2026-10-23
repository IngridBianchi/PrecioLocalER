-- ============================================================
-- PrecioLocal ER — 006_add_product_skus.sql
-- Modelo de variantes (SKU) para precio por (producto, cadena).
--
-- Incidente 2026-09-24 (ver CONTEXTO.md): Coto listaba 2 SKU
-- (531487 / 531488) con el MISMO normalized_name ("harina de
-- trigo chacabuco paquete 750 gr") y precios distintos (2129/2899);
-- el dedupe por products.normalized_name los colapsaba en un solo
-- producto y search/detail mostraban un precio por cadena
-- NO-determinístico (empate de captured_at).
--
-- Solución: tabla product_skus (id de venta real por cadena) + la
-- columna price_points.sku_id. El precio representativo por cadena
-- pasa a ser: entre los úlitmos price_points de cada SKU de esa
-- cadena, el de MENOR precio ("desde $X"). products sigue siendo el
-- item canónico deduplicado por normalized_name.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- product_skus: qué vende de verdad cada cadena (tiene su propio
-- id de venta y su propia URL de página de producto).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_skus (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id    bigint NOT NULL REFERENCES products(id),
    chain_id      bigint NOT NULL REFERENCES chains(id),
    external_id   text NOT NULL,
    name          text NOT NULL,
    url           text,
    ean           text,
    active        boolean NOT NULL DEFAULT true,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (chain_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_product_skus_product_chain
    ON product_skus (product_id, chain_id);

-- ------------------------------------------------------------
-- price_points.sku_id (nullable: puntos legacy o sin id de venta)
-- ------------------------------------------------------------
ALTER TABLE price_points
    ADD COLUMN IF NOT EXISTS sku_id bigint REFERENCES product_skus(id);

CREATE INDEX IF NOT EXISTS idx_price_points_sku_time
    ON price_points (sku_id, captured_at DESC);

-- ------------------------------------------------------------
-- Backfill: crea un SKU por (chain, external_id) tomando como
-- referencia la captura más reciente, y asigna sku_id a los puntos.
-- ------------------------------------------------------------
INSERT INTO product_skus (product_id, chain_id, external_id, name, url, first_seen_at, last_seen_at)
SELECT DISTINCT ON (ch.id, pp.raw_json->>'external_id')
       pp.product_id,
       ch.id,
       pp.raw_json->>'external_id',
       COALESCE(pp.raw_json->>'name', ''),
       pp.raw_json->>'url',
       pp.captured_at,
       pp.captured_at
FROM price_points pp
JOIN chains ch ON ch.id = pp.chain_id
WHERE pp.raw_json->>'external_id' IS NOT NULL
  AND pp.raw_json->>'external_id' <> ''
ORDER BY ch.id, pp.raw_json->>'external_id', pp.captured_at DESC;

UPDATE price_points pp
SET sku_id = s.id
FROM product_skus s
WHERE pp.chain_id = s.chain_id
  AND pp.product_id = s.product_id
  AND pp.raw_json->>'external_id' = s.external_id
  AND pp.sku_id IS NULL;

COMMIT;