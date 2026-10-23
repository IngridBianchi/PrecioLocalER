-- ============================================================
-- PrecioLocal ER — 012_analytics_service_role.sql
--
-- El motor de alertas no es un usuario final: recibe eventos de precio
-- desde Redis, busca las alertas activas de un producto (de todos los
-- usuarios) y escribe alert_events. Conectado como un rol de servicio
-- no puede depender de `app.user_id`, que 007 usa para aislar a cada
-- usuario: sin una policy de servicio, `findActiveByProductId` vería
-- cero filas y el motor nunca dispararía una alerta.
--
-- Se crea un grupo NOLOGIN propio (`pl_analytics`) en vez de reutilizar
-- `pl_ml`: ml-dotnet es un lector de precios y no debe poder leer ni
-- escribir alertas de usuarios (mismo criterio de mínimo privilegio que
-- 011 aplicó para separar pl_ml de pl_app).
--
-- El rol LOGIN (`preciolocal_analytics`) y su password viven fuera del
-- ledger, en scripts/db-service-roles.sh (`make db-roles`).
-- ============================================================

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pl_analytics') THEN
        CREATE ROLE pl_analytics NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO pl_analytics;

-- Lectura de alertas de todos los usuarios (el motor filtra por producto)
-- y actualización de last_triggered_at. No puede borrar alertas.
GRANT SELECT, UPDATE ON alerts TO pl_analytics;

-- El motor inserta el evento y el endpoint /api/alert-events lo lee
-- filtrando por userId. No puede modificar ni borrar eventos.
GRANT SELECT, INSERT ON alert_events TO pl_analytics;

-- alert_events.id es GENERATED ALWAYS AS IDENTITY.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pl_analytics;

-- Las policies existentes llaman a pl_current_user_id(); la nueva policy
-- de servicio llama a pl_is_service(). Sin EXECUTE, PostgreSQL falla con
-- "permission denied for function" antes de evaluar la policy.
GRANT EXECUTE ON FUNCTION pl_current_user_id() TO pl_analytics;
GRANT EXECUTE ON FUNCTION pl_is_service(text) TO pl_analytics;

-- Policy de servicio: puede ver todas las alertas. Las policies
-- permisivas se combinan con OR, así que `alerts_owner_all` sigue
-- aislando a los usuarios normales.
DROP POLICY IF EXISTS alerts_service_select ON alerts;
CREATE POLICY alerts_service_select ON alerts
    FOR SELECT USING (pl_is_service('pl_analytics'));

DROP POLICY IF EXISTS alerts_service_update ON alerts;
CREATE POLICY alerts_service_update ON alerts
    FOR UPDATE
    USING (pl_is_service('pl_analytics'))
    WITH CHECK (pl_is_service('pl_analytics'));

DROP POLICY IF EXISTS alert_events_service_select ON alert_events;
CREATE POLICY alert_events_service_select ON alert_events
    FOR SELECT USING (pl_is_service('pl_analytics'));

DROP POLICY IF EXISTS alert_events_service_insert ON alert_events;
CREATE POLICY alert_events_service_insert ON alert_events
    FOR INSERT WITH CHECK (pl_is_service('pl_analytics'));

COMMIT;
