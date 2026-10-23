-- ============================================================
-- PrecioLocal ER — 007_rls_revoke.sql
-- Endurecimiento de la base (WP1, docs/security.md §2/§3):
--
-- 1. RLS (A01 Broken Access Control) en las tablas con datos de
--    usuario (users/alerts/alert_events) y en las públicas
--    (price_points/predictions: lectura abierta, escritura del rol
--    de servicio).
-- 2. El aislamiento por usuario se hace con el GUC `app.user_id`,
--    seteado por la capa de conexión al resolver el JWT
--    (`SET LOCAL app.user_id = '<uuid>'` dentro de la transacción).
--    Si el GUC no está seteado, pl_current_user_id() devuelve NULL
--    → deny-by-default (no hay fuga entre tenants/usuarios).
-- 3. REVOKE de EXECUTE sobre pg_notify: el NOTIFY se emite desde el
--    trigger de 003, convertido aquí a SECURITY DEFINER para que
--    siga funcionando sin privileges en la app.
-- 4. Roles de servicio NOLOGIN (sin contraseña, sin login directo):
--    se otorga como membresía al usuario de aplicación real.
--
-- Nota de numeración: esta migración toma el 007; la de ML que
-- estaba prevista como 007 pasa a ser 008_ml.sql.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- Roles de servicio (NOLOGIN: se usan como membresías, no se
-- conectan por sí mismos ni guardan password). Se crean primero
-- porque los GRANT siguientes los referencian.
-- ------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pl_scraper') THEN
        CREATE ROLE pl_scraper NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pl_app') THEN
        CREATE ROLE pl_app NOLOGIN;
    END IF;
END
$$;

-- ------------------------------------------------------------
-- Helper: user_id del contexto, seguro ante GUC ausente/vacío.
-- Evita el cast directo (''::uuid revienta la query).
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION pl_current_user_id() RETURNS uuid
LANGUAGE plpgsql STABLE AS $$
DECLARE
    raw text;
BEGIN
    raw := NULLIF(current_setting('app.user_id', TRUE), '');
    IF raw IS NULL OR raw !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RETURN NULL;
    END IF;
    RETURN raw::uuid;
END;
$$;

REVOKE ALL ON FUNCTION pl_current_user_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pl_current_user_id() TO pl_scraper, pl_app;

-- ------------------------------------------------------------
-- 1. RLS — datos de usuario: cada uno solo ve lo suyo
-- ------------------------------------------------------------
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS users_self_select ON users;
CREATE POLICY users_self_select ON users
    FOR SELECT USING (id = pl_current_user_id());
DROP POLICY IF EXISTS users_self_update ON users;
CREATE POLICY users_self_update ON users
    FOR UPDATE
    USING (id = pl_current_user_id())
    WITH CHECK (id = pl_current_user_id());

ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS alerts_owner_all ON alerts;
CREATE POLICY alerts_owner_all ON alerts
    FOR ALL
    USING (user_id = pl_current_user_id())
    WITH CHECK (user_id = pl_current_user_id());

ALTER TABLE alert_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS alert_events_owner_select ON alert_events;
CREATE POLICY alert_events_owner_select ON alert_events
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM alerts a
            WHERE a.id = alert_events.alert_id
              AND a.user_id = pl_current_user_id()
        )
    );

-- ------------------------------------------------------------
-- 1b. RLS — catálogo/precios: lectura pública, escritura acotada
-- ------------------------------------------------------------
ALTER TABLE price_points ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS price_points_public_read ON price_points;
CREATE POLICY price_points_public_read ON price_points
    FOR SELECT USING (true);
DROP POLICY IF EXISTS price_points_service_write ON price_points;
CREATE POLICY price_points_service_write ON price_points
    FOR INSERT WITH CHECK (current_user = 'pl_scraper');
-- El DROP faltaba (incidente 2026-10-01, detectado por los integration tests
-- de ProductRepository al aplicar el esquema desde cero): replayar esta
-- migración era imposible con DuplicateObjectError.
DROP POLICY IF EXISTS price_points_service_update ON price_points;
CREATE POLICY price_points_service_update ON price_points
    FOR UPDATE USING (current_user = 'pl_scraper');
DROP POLICY IF EXISTS price_points_service_delete ON price_points;
CREATE POLICY price_points_service_delete ON price_points
    FOR DELETE USING (current_user = 'pl_scraper');

ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS predictions_public_read ON predictions;
CREATE POLICY predictions_public_read ON predictions
    FOR SELECT USING (true);
DROP POLICY IF EXISTS predictions_service_write ON predictions;
CREATE POLICY predictions_service_write ON predictions
    FOR INSERT WITH CHECK (current_user IN ('pl_scraper', 'pl_app'));

-- ------------------------------------------------------------
-- 2. pg_notify: solo desde el trigger, que pasa a ser
-- SECURITY DEFINER (si no, quitar el EXECUTE rompe el NOTIFY).
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION notify_price_point()
RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify(
        'pl_price_point',
        json_build_object('price_point_id', NEW.id, 'product_id', NEW.product_id)::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

REVOKE EXECUTE ON FUNCTION pg_notify(text, text) FROM PUBLIC;

-- ------------------------------------------------------------
-- 3. Privilegios mínimos por rol (A05 Security Misconfiguration)
-- ------------------------------------------------------------
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM pl_scraper, pl_app;
GRANT USAGE ON SCHEMA public TO pl_scraper, pl_app;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM pl_scraper, pl_app;

-- Lectura de catálogo para ambos servicios.
GRANT SELECT ON chains, stores, categories, brands, products TO pl_scraper, pl_app;

-- El scraper escribe precios y predicciones; nunca toca usuarios.
GRANT SELECT, INSERT, UPDATE, DELETE ON price_points TO pl_scraper;
GRANT SELECT, INSERT ON predictions TO pl_scraper;

-- El backend de aplicación gestiona usuarios/alertas.
GRANT SELECT ON users, alerts, alert_events TO pl_app;
GRANT INSERT, UPDATE, DELETE ON alerts TO pl_app;
GRANT INSERT ON alert_events TO pl_app;

-- Sequences: identity columns de price_points/predictions/alerts.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pl_scraper, pl_app;

-- Sin NOTIFY desde la app.
REVOKE EXECUTE ON FUNCTION pg_notify(text, text) FROM pl_scraper, pl_app;

COMMIT;
