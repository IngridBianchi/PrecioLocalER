-- ============================================================
-- PrecioLocal ER — 001_init.sql
-- Schema base: chains, stores, categories, brands, products,
-- price_points, predictions, users, alerts, alert_events.
-- TODO transaccional; PostgreSQL 16 / Supabase compatible.
-- Fuente de verdad → docs/contracts.md
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- Extensiones
-- ------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- búsqueda trigram/trigram
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- gen_random_uuid()

-- ------------------------------------------------------------
-- chains (cadenas de supermercados)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chains (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            text NOT NULL,
    slug            text NOT NULL UNIQUE,
    domain          text,
    base_url        text,
    region          text NOT NULL DEFAULT 'entre_rios',
    active          boolean NOT NULL DEFAULT true,
    scrape_schedule text,                     -- cron de apscheduler
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- stores (sucursales)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stores (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chain_id   bigint NOT NULL REFERENCES chains(id),
    name       text NOT NULL,
    city       text NOT NULL,
    address    text,
    lat        double precision,
    lng        double precision,
    active     boolean NOT NULL DEFAULT true
);

-- ------------------------------------------------------------
-- categories (jerárquico)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS categories (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name      text NOT NULL,
    slug      text NOT NULL UNIQUE,
    parent_id bigint REFERENCES categories(id)
);

-- ------------------------------------------------------------
-- brands
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS brands (
    id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    slug text NOT NULL UNIQUE
);

-- ------------------------------------------------------------
-- products
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           text NOT NULL,
    normalized_name text NOT NULL,              -- lowercase, sin tildes, orden estable
    size_quantity  numeric(10,3),               -- ej: 0.9 (900cc), 1 (1kg)
    size_unit      text NOT NULL DEFAULT 'und', -- kg | g | lt | ml | und | pack
    brand_id       bigint REFERENCES brands(id),
    category_id    bigint REFERENCES categories(id),
    ean            text UNIQUE,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_products_name_trgm ON products USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_products_norm_trgm ON products USING gin (normalized_name gin_trgm_ops);

-- ------------------------------------------------------------
-- price_points
-- hash = sha256(producto|precio|captura) → fuente de dedupe
-- Unique constraint = idempotencia del scraper (ON CONFLICT hash)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS price_points (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id  bigint NOT NULL REFERENCES products(id),
    store_id    bigint REFERENCES stores(id),
    chain_id    bigint NOT NULL REFERENCES chains(id),
    price       numeric(12,2) NOT NULL CHECK (price >= 0),
    promo_type  text,
    captured_at timestamptz NOT NULL,
    scraped_at  timestamptz NOT NULL DEFAULT now(),
    raw_json    jsonb NOT NULL,
    hash        text NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_price_points_product_time ON price_points (product_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_points_chain_time ON price_points (chain_id, captured_at DESC);

-- ------------------------------------------------------------
-- predictions
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS predictions (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id      bigint NOT NULL REFERENCES products(id),
    model           text NOT NULL,              -- sarimax | holtwinters | mlnet
    forecast_date   timestamptz NOT NULL,
    predicted_price numeric(12,2) NOT NULL,
    conf_low        numeric(12,2),
    conf_high       numeric(12,2),
    trained_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_predictions_product_date ON predictions (product_id, forecast_date);

-- ------------------------------------------------------------
-- users
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email        text NOT NULL UNIQUE,
    device_token text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- alerts
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alerts (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           uuid NOT NULL REFERENCES users(id),
    product_id        bigint NOT NULL REFERENCES products(id),
    direction         text NOT NULL CHECK (direction IN ('below','above')),
    target_price      numeric(12,2) NOT NULL,
    active            boolean NOT NULL DEFAULT true,
    last_triggered_at timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, product_id, direction)
);

-- ------------------------------------------------------------
-- alert_events
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_events (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    alert_id       bigint NOT NULL REFERENCES alerts(id),
    price_point_id bigint REFERENCES price_points(id),
    price          numeric(12,2) NOT NULL,
    triggered_at   timestamptz NOT NULL DEFAULT now()
);

COMMIT;