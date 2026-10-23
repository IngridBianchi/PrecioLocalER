#!/usr/bin/env bash
# ============================================================
# PrecioLocal ER — scripts/db-service-roles.sh
#
# Crea (o reaplica) los roles LOGIN de servicio y sus membresías.
# Idempotente: correrlo dos veces no cambia nada; para cambiar una
# password usá `scripts/db-rotate-roles.sh`.
#
# POR QUÉ ESTÁ FUERA DE LAS MIGRACIONES
# ------------------------------------
# Los roles de grupo (`pl_scraper`, `pl_app`, `pl_ml`) están en
# 011_service_roles.sql porque son parte del esquema. Las passwords no pueden
# estar ahí: el ledger de migraciones es un archivo versionado, y una password
# en un archivo versionado es una password filtrada. Lo que sí se versiona es
# *qué* roles existen y a qué grupo pertenecen.
#
# El rol que corre esto tiene que ser el superusuario del contenedor
# (POSTGRES_USER), porque crear roles y dar membresías es tarea de admin. El
# servicio NO puede hacerlo por su cuenta: si pudiera crear su propio rol, la
# separación de privilegios de 011 no valdría nada.
#
# Uso:
#   scripts/db-service-roles.sh
#   SCRAPER_DB_PASSWORD=... ML_DB_PASSWORD=... scripts/db-service-roles.sh
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE="${ENV_FILE:-.env}"

[[ -f "$ENV_FILE" ]] || { echo "error: no existe $ENV_FILE" >&2; exit 1; }

# Lee una clave de .env sin ejecutar el archivo (`source` correría cualquier
# cosa que alguien pegue ahí; es un script de desarrollo, pero no hace falta
# abrir esa puerta).
env_get() {
    # El `tr -d '\r'` no es cosmético: un .env guardado desde Windows tiene
    # CRLF, y sin sacarlo el `\r` se va al final del valor. El síntoma es
    # desconcertante —psql busca un rol llamado "preciolocal<CR>" y dice que no
    # existe, cuando el rol existe y la password es la correcta—.
    sed -n "s/^[[:space:]]*$1=\(.*\)\$/\1/p" "$ENV_FILE" \
        | tail -1 | tr -d '\r' | sed 's/^["'\'']//; s/["'\'']$//'
}

ADMIN_USER="$(env_get POSTGRES_USER)"; ADMIN_USER="${ADMIN_USER:-preciolocal}"
DB_NAME="$(env_get POSTGRES_DB)"; DB_NAME="${DB_NAME:-preciolocal}"
SCRAPER_USER="$(env_get SCRAPER_DB_USER)"; SCRAPER_USER="${SCRAPER_USER:-preciolocal_scraper}"
ML_USER="$(env_get ML_DB_USER)"; ML_USER="${ML_USER:-preciolocal_ml}"
ANALYTICS_USER="$(env_get ANALYTICS_DB_USER)"; ANALYTICS_USER="${ANALYTICS_USER:-preciolocal_analytics}"
ANALYTICS_GROUP="pl_analytics"
SCRAPER_PW="$(env_get SCRAPER_DB_PASSWORD)"
ML_PW="$(env_get ML_DB_PASSWORD)"
ANALYTICS_PW="$(env_get ANALYTICS_DB_PASSWORD)"

MISSING=0
[[ -n "$SCRAPER_PW" ]] || { echo "  falta SCRAPER_DB_PASSWORD" >&2; MISSING=1; }
[[ -n "$ML_PW" ]] || { echo "  falta ML_DB_PASSWORD" >&2; MISSING=1; }
[[ -n "$ANALYTICS_PW" ]] || { echo "  falta ANALYTICS_DB_PASSWORD" >&2; MISSING=1; }
if (( MISSING )); then
    cat >&2 <<EOF

El scraper, ml-dotnet y analytics-java ya no conectan como superusuario (P0.1), así que
necesitan credenciales propias. Agregá al final de $ENV_FILE:

  SCRAPER_DB_USER=$SCRAPER_USER
  SCRAPER_DB_PASSWORD=<largo y aleatorio>
  ML_DB_USER=$ML_USER
  ML_DB_PASSWORD=<otro distinto>
  ANALYTICS_DB_USER=$ANALYTICS_USER
  ANALYTICS_DB_PASSWORD=<otro distinto>

Para generarlas y escribirlas por vos: scripts/db-rotate-roles.sh
EOF
    exit 1
fi

if [[ "$SCRAPER_PW" == "$ML_PW" || "$SCRAPER_PW" == "$ANALYTICS_PW" || "$ML_PW" == "$ANALYTICS_PW" ]]; then
    echo "error: hay passwords duplicadas entre servicios." >&2
    echo "      Un mismo secreto para dos servicios anula la separación." >&2
    exit 1
fi

psql_admin() {
    docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 \
        -U "$ADMIN_USER" -d "$DB_NAME" "$@"
}

# ------------------------------------------------------------
# 1. Crear los LOGIN y fijarles la password.
#
#    Se crea con NOSUPERUSER/NOBYPASSRLS/INHERIT explícitos aunque el rol
#    siguiente no existiera: `CREATE ROLE IF NOT EXISTS` no arregla un rol que
#    alguien creó antes como superusuario, y sin INHERIT la membresía existe
#    pero los GRANTs no se heredan (que es justo lo que `pl_is_service`
#    chequea). Fijar los atributos en cada corrida deja el rol en el estado
#    que la migración 011 exige.
#
#    `ALTER ROLE ... PASSWORD` es la rotación: mismo camino, otro valor.
# ------------------------------------------------------------
# Cómo se pasan las variables a psql (medido, no supuesto).
#
# `-c "SELECT ... :'x'"` NO interpola: psql trata cada `-c` como una unidad
# independiente y no le aplica la sustitución de variables. Lo mismo pasa dentro
# de un `DO $$ ... $$`. El único camino que interpola es el SQL por stdin, y
# por eso todo lo de abajo va por heredoc.
#
# Las dos sustituciones que sí sirven por stdin:
#   :'x'  → literal de texto  (passwords: es el escapado correcto, y la
#            comilla simple de PostgreSQL previene la inyección)
#   :"x"  → identificador entre comillas dobles
valid_ident() {
    [[ "$1" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || {
        echo "error: '$1' no es un identificador de rol válido" >&2
        exit 1
    }
}

role_exists() {
    local out
    out="$(docker compose exec -T postgres psql -At -q -U "$ADMIN_USER" \
        -d "$DB_NAME" -v role="$1" <<'SQL'
SELECT 1 FROM pg_roles WHERE rolname = :'role';
SQL
)"
    [[ "$out" == "1" ]]
}

group_exists() {
    local out
    out="$(docker compose exec -T postgres psql -At -q -U "$ADMIN_USER" \
        -d "$DB_NAME" -v grp="$1" <<'SQL'
SELECT 1 FROM pg_roles WHERE rolname = :'grp';
SQL
)"
    [[ "$out" == "1" ]]
}

apply_role() {
    local role="$1" password="$2" group="$3"
    local attrs="LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
    local verb="CREATE ROLE"

    valid_ident "$role"
    valid_ident "$group"

    # ALTER vs CREATE: `CREATE ROLE IF NOT EXISTS` no existe, y si el rol ya
    # estaba con privilegios de más hay que corregirlos igual.
    if role_exists "$role"; then
        verb="ALTER ROLE"
    fi

    psql_admin -v role="$role" -v pw="$password" -v grp="$group" <<SQL
$verb :"role" $attrs PASSWORD :'pw';
-- Un LOGIN debe pertenecer sólo a su grupo de servicio. Si antes se le dio
-- otro (p. ej. analytics heredaba pl_ml), se revoca para que el permiso más
-- grande no quede escondido detrás de una membresía vieja.
REVOKE pl_scraper, pl_app, pl_ml, pl_analytics FROM :"role";
GRANT :"grp" TO :"role";
SQL
}

echo ">> rol de servicio del scraper: $SCRAPER_USER (miembro de pl_scraper)"
apply_role "$SCRAPER_USER" "$SCRAPER_PW" pl_scraper

echo ">> rol de servicio de ml-dotnet: $ML_USER (miembro de pl_ml)"
apply_role "$ML_USER" "$ML_PW" pl_ml

echo ">> rol de servicio de analytics-java: $ANALYTICS_USER (miembro de $ANALYTICS_GROUP)"
apply_role "$ANALYTICS_USER" "$ANALYTICS_PW" "$ANALYTICS_GROUP"

# Los grupos los crean las migraciones (011 y 012). Si el ledger todavía no
# llegó hasta ahí, este script no puede aplicar la membresía; avisarlo claro
# es mejor que fallar con un "role does not exist" que parece un bug del script.
if ! group_exists "$ANALYTICS_GROUP"; then
    cat >&2 <<EOF

error: el grupo $ANALYTICS_GROUP todavía no existe en la base.

  Las migraciones crean los grupos NOLOGIN (pl_scraper/pl_app/pl_ml/pl_analytics);
  este script solo crea los usuarios que se conectan. Faltaría aplicar 011/012:

    ./scripts/pl.sh migrate

  O, en una base nueva, todo en el orden correcto:
    ./scripts/pl.sh db-setup
EOF
    exit 1
fi

# ------------------------------------------------------------
# 2. Verificación: los tres estados que importan.
#    - el rol existe y NO puede saltarse la RLS
#    - la membresía al grupo está
#    - la conexión funciona de verdad, no solo el SQL
# ------------------------------------------------------------
verify() {
    local role="$1" password="$2"
    psql_admin -v role="$role" <<'SQL'
\pset tuples_only on
\pset format unaligned
SELECT format(
    '   %-22s login=%s super=%s bypassrls=%s grupos=%s',
    r.rolname,
    r.rolcanlogin,
    r.rolsuper,
    r.rolbypassrls,
    COALESCE(string_agg(g.rolname, ',' ORDER BY g.rolname)
             FILTER (WHERE g.rolname IN ('pl_scraper', 'pl_app', 'pl_ml', 'pl_analytics')),
             '(ninguno)')
)
FROM pg_roles r
LEFT JOIN pg_auth_members m ON m.member = r.oid
LEFT JOIN pg_roles g ON g.oid = m.roleid
WHERE r.rolname = :'role'
GROUP BY r.oid, r.rolname, r.rolcanlogin, r.rolsuper, r.rolbypassrls;
SQL

    if docker compose exec -T -e PGPASSWORD="$password" postgres \
        psql -q -t -A -U "$role" -d "$DB_NAME" -c 'SELECT 1' >/dev/null 2>&1; then
        echo "   conexión OK"
    else
        echo "   FALLA la conexión: revisá la password de $role" >&2
        exit 1
    fi
}

echo ">> verificando:"
verify "$SCRAPER_USER" "$SCRAPER_PW"
verify "$ML_USER" "$ML_PW"
verify "$ANALYTICS_USER" "$ANALYTICS_PW"

echo ">> listo. Si cambiaron passwords, recreá los servicios que las usan:"
echo "   docker compose up -d --force-recreate scraper ml-dotnet analytics-java"
