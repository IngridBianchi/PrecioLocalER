-- ============================================================
-- PrecioLocal ER — 003_indexes_triggers.sql
-- Índices operativos + trigger de NOTIFY (LISTEN/NOTIFY)
-- consumido por el AlertEngine Java (ADR-0003).
-- ============================================================

BEGIN;

-- btree_gist habilita operator classes btree para GiST (series en bigint+timestamptz)
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- tstzrange en price_points → eficiencia de series
CREATE INDEX IF NOT EXISTS idx_price_points_gtree ON price_points USING gist (product_id, captured_at);

-- predicciones: consultas por modelo (quién hizo el forecast)
CREATE INDEX IF NOT EXISTS idx_predictions_model ON predictions (model, forecast_date);

-- alertas activas por producto (scan diario del engine)
CREATE INDEX IF NOT EXISTS idx_alerts_active_product ON alerts (active, product_id);

-- ------------------------------------------------------------
-- Trigger: al insertar un price_point, NOTIFY a Java.
-- ============================================================
CREATE OR REPLACE FUNCTION notify_price_point()
RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify(
        'pl_price_point',
        json_build_object('price_point_id', NEW.id, 'product_id', NEW.product_id)::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_price_point_notify ON price_points;
CREATE TRIGGER trg_price_point_notify
AFTER INSERT ON price_points
FOR EACH ROW EXECUTE FUNCTION notify_price_point();

COMMIT;