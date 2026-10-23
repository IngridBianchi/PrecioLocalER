-- ============================================================
-- PrecioLocal ER — 013_analytics_alert_crud.sql
--
-- El gateway expone CRUD de alertas a través del rol de servicio
-- pl_analytics (analytics-java). Para soportar alta y baja de alertas
-- desde la UI se otorgan INSERT/DELETE sobre alerts y las policies
-- correspondientes, manteniendo el aislamiento de usuarios normales.
-- ============================================================

BEGIN;

GRANT INSERT, DELETE ON alerts TO pl_analytics;

DROP POLICY IF EXISTS alerts_service_insert ON alerts;
CREATE POLICY alerts_service_insert ON alerts
    FOR INSERT WITH CHECK (pl_is_service('pl_analytics'));

DROP POLICY IF EXISTS alerts_service_delete ON alerts;
CREATE POLICY alerts_service_delete ON alerts
    FOR DELETE USING (pl_is_service('pl_analytics'));

COMMIT;
