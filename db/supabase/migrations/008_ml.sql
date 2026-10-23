-- ============================================================
-- PrecioLocal ER — 008_ml.sql
-- Persistencia de los modelos ML de Fases 2-3 (ADR-0006).
--
-- `predictions` (001) era la tabla original del diseño. Aquí se
-- completa el esquema de ML:
--   - forecasts: predicciones Python (SARIMAX / HoltWinters)
--   - ml_models: registry con métricas para el comparativo
--     real de ADR-0006 (WP5)
--   - anomalies: salida del IsolationForest sobre outliers
--
-- RLS: lectura pública para las predicciones (no son PII) y
-- escritura acotada a los roles de servicio, igual que 007.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- forecasts: predicción de precio por producto/cadena/modelo
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS forecasts (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id      bigint NOT NULL REFERENCES products(id),
    chain_id        bigint NOT NULL REFERENCES chains(id),
    model           text NOT NULL CHECK (model IN ('sarimax', 'holtwinters', 'mlnet')),
    forecast_date   date NOT NULL,
    predicted_price numeric(12,2) NOT NULL CHECK (predicted_price >= 0),
    conf_low        numeric(12,2),
    conf_high       numeric(12,2),
    horizon_days    int NOT NULL DEFAULT 1 CHECK (horizon_days > 0),
    trained_at      timestamptz NOT NULL DEFAULT now(),
    -- Un modelo no puede reescribir la predicción de la misma fecha:
    -- la persistencia es idempotente (ON CONFLICT DO UPDATE).
    UNIQUE (product_id, chain_id, model, forecast_date)
);

CREATE INDEX IF NOT EXISTS idx_forecasts_product_date
    ON forecasts (product_id, forecast_date DESC);
CREATE INDEX IF NOT EXISTS idx_forecasts_chain_model
    ON forecasts (chain_id, model, forecast_date DESC);

-- ------------------------------------------------------------
-- ml_models: registry de modelos entrenados + métricas
-- (habilita el comparativo real Python vs .NET del ADR-0006)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ml_models (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model          text NOT NULL,
    language       text NOT NULL CHECK (language IN ('python', 'dotnet')),
    product_id     bigint NOT NULL REFERENCES products(id),
    chain_id       bigint NOT NULL REFERENCES chains(id),
    -- Métricas de la validación (walk-forward sobre price_points).
    mape           numeric(8,4),
    rmse           numeric(12,4),
    mae            numeric(12,4),
    n_points       int NOT NULL DEFAULT 0,
    trained_at     timestamptz NOT NULL DEFAULT now(),
    is_active      boolean NOT NULL DEFAULT true,
    UNIQUE (model, language, product_id, chain_id)
);

CREATE INDEX IF NOT EXISTS idx_ml_models_comparison
    ON ml_models (product_id, chain_id, is_active, mape);

-- ------------------------------------------------------------
-- anomalies: outliers detectados por IsolationForest
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS anomalies (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id     bigint NOT NULL REFERENCES products(id),
    chain_id       bigint NOT NULL REFERENCES chains(id),
    price_point_id bigint REFERENCES price_points(id),
    captured_at    timestamptz NOT NULL,
    price          numeric(12,2) NOT NULL,
    score          double precision NOT NULL,
    is_anomaly     boolean NOT NULL DEFAULT true,
    detected_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (product_id, chain_id, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_anomalies_product_time
    ON anomalies (product_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_anomalies_flagged
    ON anomalies (is_anomaly, detected_at DESC)
    WHERE is_anomaly;

-- ------------------------------------------------------------
-- RLS (mismo criterio que 007)
-- ------------------------------------------------------------
ALTER TABLE forecasts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS forecasts_public_read ON forecasts;
CREATE POLICY forecasts_public_read ON forecasts
    FOR SELECT USING (true);
DROP POLICY IF EXISTS forecasts_service_write ON forecasts;
CREATE POLICY forecasts_service_write ON forecasts
    FOR INSERT WITH CHECK (current_user IN ('pl_scraper', 'pl_app'));

ALTER TABLE ml_models ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ml_models_public_read ON ml_models;
CREATE POLICY ml_models_public_read ON ml_models
    FOR SELECT USING (true);
DROP POLICY IF EXISTS ml_models_service_write ON ml_models;
CREATE POLICY ml_models_service_write ON ml_models
    FOR INSERT WITH CHECK (current_user IN ('pl_scraper', 'pl_app'));

ALTER TABLE anomalies ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS anomalies_public_read ON anomalies;
CREATE POLICY anomalies_public_read ON anomalies
    FOR SELECT USING (true);
DROP POLICY IF EXISTS anomalies_service_write ON anomalies;
CREATE POLICY anomalies_service_write ON anomalies
    FOR INSERT WITH CHECK (current_user IN ('pl_scraper', 'pl_app'));

GRANT SELECT ON forecasts, ml_models, anomalies TO pl_scraper, pl_app;
GRANT INSERT ON forecasts, ml_models, anomalies TO pl_scraper, pl_app;

COMMIT;
