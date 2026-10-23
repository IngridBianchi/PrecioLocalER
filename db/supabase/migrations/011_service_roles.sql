-- ============================================================
-- PrecioLocal ER — 011_service_roles.sql
-- P0.1 de MEJORAS.md: que la RLS deje de ser decorativa.
--
-- EL PROBLEMA (auditado 2026-10-02)
-- -------------------------------
-- 007/008/010 dejaron el diseño correcto escrito en SQL: dos roles de
-- servicio NOLOGIN (`pl_scraper`, `pl_app`), GRANTs mínimos, RLS habilitada y
-- policies que comparan `current_user` contra el rol de servicio. Todo eso
-- estaba **inerte**, por dos razones independientes:
--
--   1. Nadie se conectaba con esos roles. El scraper y ml-dotnet usan
--      `DATABASE_URL`, que componía con `POSTGRES_USER` = `preciolocal`, y ese
--      rol es superusuario del contenedor (así lo crea la imagen oficial).
--      Un superusuario tiene `rolbypassrls = t`: se saltea la RLS entera, sin
--      importar cuántas policies haya ni aunque la tabla tenga FORCE.
--
--   2. Aun entrando con el rol correcto, las policies eran inalcanzables.
--      Este es el hallazgo que no se ve mirando el SQL:
--
--          CREATE POLICY price_points_service_write ON price_points
--              FOR INSERT WITH CHECK (current_user = 'pl_scraper');
--
--      `current_user` es el rol con el que estás *conectado*, no el rol del
--      que heredás privilegios. Si el login role es miembro de `pl_scraper`
--      (que es como corresponde: los roles NOLOGIN son membresías, no
--      usuarios), entonces `current_user` vale `preciolocal_scraper` y la
--      comparación da FALSE. Verificado empíricamente antes de escribir esto:
--
--          ERROR: new row violates row-level security policy for table
--                 "price_points"
--
--      O sea: el diseño de 007 no fallaba por un detalle, fallaba siempre que
--      se lo usara como estaba pensado.
--
-- LA CORRECCIÓN (dos partes, y hacen falta las dos)
-- -------------------------------------------------
--  A. `pl_is_service()`: "¿esta conexión está actuando effectively como el rol
--     de servicio X?" — responde con `pg_has_role(..., 'USAGE')`, que es
--     true para una membresía con INHERIT (el default) y false para NOINHERIT.
--     Se usa `USAGE` y no `MEMBER` a propósito: con NOINHERIT la membresía
--     existe pero los GRANTs **no** se heredan, así que la conexión tampoco
--     puede escribir igual. Los dos chequeos coinciden; `USAGE` describe
--     mejor la intención y falla cerrado.
--
--     No se arregla con `SET ROLE` porque obligaría a tocar la capa de
--     conexión de los tres servicios (asyncpg, Npgsql/EF) para hacer un
--     `SET LOCAL ROLE` por transacción. Con `pg_has_role` alcanza con la
--     membresía y el pool no se entera.
--
--  B. Los roles LOGIN reales, sus passwords y las membresías: eso NO puede
--     vivir en una migración, porque una contraseña en el ledger versionado es
--     peor que un rol sin usable. Va en `scripts/db-service-roles.sh`
--     (`make db-roles`), que es idempotente y **rotar la password es correrlo
--     otra vez**.
--
-- Lo que esta migración NO hace, a propósito:
--   - No transfiere la propiedad de las tablas a un rol `pl_owner`. La RLS no
--     la defeated el ser dueño, la defeated ser superusuario. Y el owner real
--     del harness de tests (`preciolocal_test`, NO superusuario) aplica esto
--     mismo: si las tablas fueran de `pl_owner`, necesitaría ADMIN OPTION
--     paralas soltar en el `DROP DATABASE` de teardown, y el replay de
--     migraciones en una base limpia volvería a ser imposible.
--   - No crea los roles LOGIN (ver B).
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1. Helper: ¿esta conexión es (o hereda de) el rol de servicio?
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION pl_is_service(role_name text) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT pg_has_role(current_user, role_name, 'USAGE');
$$;

-- Por defecto PostgreSQL da EXECUTE al PUBLIC sobre las funciones nuevas, y
-- `pl_is_service` se usa dentro de policies: sin este REVOKE, cualquier rol
-- del cluster (incluido un `anon` futuro) podría consultarla. No es un
-- escalada grave —solo dice si sos miembro de algo que ya conocés— pero el
-- criterio de este repo es mínimo privilegio, no "no es grave".
REVOKE ALL ON FUNCTION pl_is_service(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pl_is_service(text) TO pl_scraper, pl_app;

-- ------------------------------------------------------------
-- 2. Tercer rol de servicio: el lector de ml-dotnet.
--
-- .NET no escribe: lee `price_points`, `products`, `chains` y `ml_models`
-- (EF Core sobre `PriceDbContext`, en `PriceHistoryRepository` y
-- `ReportService`). No tiene su propio grupo porque hasta ahora reutilizaba
-- `pl_app`, que además puede escribir en `alerts`/`users`. Mezclar "leer
-- precios" con "gestionar alertas" no es una GRANT cualquiera: es el motivo
-- por el que el permiso más chico no sirve de nada si se comparte con el
-- permiso más grande.
-- ------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pl_ml') THEN
        CREATE ROLE pl_ml NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO pl_ml;
GRANT SELECT ON products, chains, price_points, predictions, forecasts, ml_models TO pl_ml;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pl_ml;

-- ------------------------------------------------------------
-- 3. Policies reescritas: `current_user = 'X'` → `pl_is_service('X')`.
--
-- Sólo se tocan las policies de escritura de los roles de servicio. Las de
-- lectura (`*_public_read`) y las de aislamiento por usuario (`users_*`,
-- `alerts_owner_all`, `alert_events_owner_select`) no comparan contra un rol y
-- no tienen el bug: siguen igual, y `TO public` es lo correcto ahí (la regla
-- aplica a todos, y lo que decide el acceso es el USING).
-- ------------------------------------------------------------
DROP POLICY IF EXISTS price_points_service_write ON price_points;
CREATE POLICY price_points_service_write ON price_points
    FOR INSERT WITH CHECK (pl_is_service('pl_scraper'));

DROP POLICY IF EXISTS price_points_service_update ON price_points;
CREATE POLICY price_points_service_update ON price_points
    FOR UPDATE USING (pl_is_service('pl_scraper'));

DROP POLICY IF EXISTS price_points_service_delete ON price_points;
CREATE POLICY price_points_service_delete ON price_points
    FOR DELETE USING (pl_is_service('pl_scraper'));

DROP POLICY IF EXISTS predictions_service_write ON predictions;
CREATE POLICY predictions_service_write ON predictions
    FOR INSERT WITH CHECK (pl_is_service('pl_scraper'));

DROP POLICY IF EXISTS forecasts_service_write ON forecasts;
CREATE POLICY forecasts_service_write ON forecasts
    FOR INSERT WITH CHECK (pl_is_service('pl_scraper'));

DROP POLICY IF EXISTS anomalies_service_write ON anomalies;
CREATE POLICY anomalies_service_write ON anomalies
    FOR INSERT WITH CHECK (pl_is_service('pl_scraper'));

DROP POLICY IF EXISTS ml_models_service_write ON ml_models;
CREATE POLICY ml_models_service_write ON ml_models
    FOR INSERT WITH CHECK (pl_is_service('pl_scraper'));

-- ------------------------------------------------------------
-- 4. GRANTs que faltaban, del mismo hueco que 010 cerró para product_skus.
--
-- El scraper hace upsert de catálogo (`products`, ver `upsert_product`), y
-- 007 sólo le daba SELECT. Con el rol de servicio real esto no es un
-- faltante teórico: el primer scrape fallaría en la primera escritura.
--
-- `ml_models`/`forecasts`/`anomalies`: 008 les dab INSERT a pl_scraper *y* a
-- pl_app porque pl_app era el único lector. Ahora que pl_ml existe, pl_app
-- conserva INSERT porque sigue siendo el que decide cuándo reentrenar
-- (`POST /admin/retrain` escribe forecasts).
-- ------------------------------------------------------------
GRANT INSERT, UPDATE ON products TO pl_scraper;
GRANT SELECT, INSERT ON products TO pl_app;
GRANT SELECT, INSERT ON forecasts, ml_models, anomalies TO pl_app;

-- El scraper no necesita tocar el catálogo de cadenas/tiendas ni borrarlo:
-- `chains`, `stores`, `categories` y `brands` son seeds administrados a mano.
GRANT SELECT ON chains, stores, categories, brands TO pl_ml;

-- ------------------------------------------------------------
-- 5. Defensa de profundidad: ninguna tabla del schema público queda accesible
-- a PUBLIC. Por defecto PostgreSQL no otorga nada al PUBLIC en tablas nuevas,
-- así que hoy esto es un no-op defensivo — pero es la línea de base que hace
--_visible una regresión si alguien agrega un GRANT ON ... TO PUBLIC.
-- ------------------------------------------------------------
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;

-- ------------------------------------------------------------
-- 6. Guardarraíl: un rol de servicio que sea superusuario (o que pueda
-- saltarse la RLS) anula todo lo de arriba sin dejar rastro en el SQL.
-- Se chequea en la propia migración para que el fallo sea ruidoso y temprano
-- en vez de una falsa sensación de seguridad.
--
-- Chequea los LOGIN miembros de los grupos de servicio, que es exactamente el
-- conjunto que va a usar la app. `rolsuper`/`rolbypassrls` a nivel de cluster,
-- así que se miran `pg_roles` (no hay filtro por base).
-- ------------------------------------------------------------
DO $$
DECLARE
    offender text;
BEGIN
    SELECT string_agg(DISTINCT r.rolname, ', ' ORDER BY r.rolname)
      INTO offender
      FROM pg_roles r
      JOIN pg_auth_members m ON m.member = r.oid
      JOIN pg_roles g ON g.oid = m.roleid
     WHERE r.rolcanlogin
       AND (r.rolsuper OR r.rolbypassrls)
       AND g.rolname IN ('pl_scraper', 'pl_app', 'pl_ml');

    IF offender IS NOT NULL THEN
        RAISE EXCEPTION
            'P0.1: estos roles de servicio pueden saltarse la RLS: %', offender
            USING HINT = 'Un rol de servicio LOGIN con rolsuper o rolbypassrls '
                         'deshace esta migración. Corregí el rol, no la policy.';
    END IF;
END
$$;

COMMIT;
