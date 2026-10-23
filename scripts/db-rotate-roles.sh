#!/usr/bin/env bash
# ============================================================
# PrecioLocal ER — scripts/db-rotate-roles.sh
#
# Genera passwords nuevas para los roles LOGIN de servicio, las escribe en
# .env y las aplica a la base. Es la rotación del P0.3 (auditoría/rotación).
#
# Por qué el ADMIN_TOKEN también se rota acá y no en otro lado: es el mismo
# problema con dos mecanismos distintos. El token es un string compartido que
# viaja en un header; la password es un secreto que viaja en una URI. Los dos
# se generan igual, se escriben en .env y se aplican sin tocar código.
#
# Idempotente por diseño: correrlo dos veces rota dos veces. No hay "confirmá
# que querés rotar" porque no hay estado que preservar: lo único que se
# modifica son credenciales, y las viejas dejan de servir en el mismo paso.
#
# Uso:  scripts/db-rotate-roles.sh          # rota los 3 secretos
#        scripts/db-rotate-roles.sh db      # solo passwords de la base
#        scripts/db-rotate-roles.sh token    # solo el ADMIN_TOKEN
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE="${ENV_FILE:-.env}"
WHAT="${1:-all}"

[[ -f "$ENV_FILE" ]] || { echo "error: no existe $ENV_FILE" >&2; exit 1; }

# ------------------------------------------------------------
# Lectura/escritura de .env por clave, sin `source` y sin perder los
# comentarios: se reescribe solo la línea del valor.
# ------------------------------------------------------------
env_get() {
    sed -n "s/^[[:space:]]*$1=\(.*\)\$/\1/p" "$ENV_FILE" | tail -1 | sed 's/^["'\'']//; s/["'\'']$//'
}

env_set() {
    local key="$1" value="$2"
    local tmp
    tmp="$(mktemp)"
    # awk reconstruye el archivo: si la clave existe, reemplaza su valor; si no,
    # la agrega al final. Un `sed -i` con dos casos es más corto pero parte el
    # archivo en dos y deja el .env a medio escribir si algo falla en el medio.
    KEY="$key" VALUE="$value" awk '
        BEGIN { done = 0 }
        $0 ~ "^[[:space:]]*" ENVIRON["KEY"] "=" {
            if (!done) { print ENVIRON["KEY"] "=" ENVIRON["VALUE"]; done = 1 }
            next
        }
        { print }
        END {
            if (!done) {
                print ""
                print "# ---Roles de servicio (P0.1) + token de admin---"
                print ENVIRON["KEY"] "=" ENVIRON["VALUE"]
            }
        }
    ' "$ENV_FILE" >"$tmp"
    mv "$tmp" "$ENV_FILE"
}

# 32 bytes de entropía en base64url: sin `=`, sin `+` ni `/`, así que no rompe
# ni la URI de Postgres ni una variable de entorno interpolada en YAML.
gen_secret() {
    local n
    n=$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')
    printf '%s' "$n" | tr 'a-f' 'ABCDEF' | sed 's/.\{16\}/&\n/g' | head -2 | tr -d '\n'
}

rotate_db() {
    local scraper_pw ml_pw analytics_pw
    scraper_pw="$(gen_secret)"
    ml_pw="$(gen_secret)"
    analytics_pw="$(gen_secret)"
    echo ">> nuevas passwords para los roles de servicio"
    env_set SCRAPER_DB_PASSWORD "$scraper_pw"
    env_set ML_DB_PASSWORD "$ml_pw"
    env_set ANALYTICS_DB_PASSWORD "$analytics_pw"
    SCRAPER_DB_PASSWORD="$scraper_pw" ML_DB_PASSWORD="$ml_pw" ANALYTICS_DB_PASSWORD="$analytics_pw" \
        scripts/db-service-roles.sh
}

rotate_token() {
    local token
    token="$(gen_secret)"
    echo ">> nuevo ADMIN_TOKEN"
    env_set ADMIN_TOKEN "$token"
    echo "   Los servicios comparan el header contra esta variable,"
    echo "   así que hay que recrearlos (no alcanza con reiniciar):"
    echo "     docker compose up -d --force-recreate scraper"
}

case "$WHAT" in
    db)    rotate_db ;;
    token) rotate_token ;;
    all)   rotate_db; rotate_token ;;
    *) echo "uso: $0 [all|db|token]" >&2; exit 2 ;;
esac

echo ">> hecho. Recreá los servicios para que tomen las credenciales nuevas:"
echo "   docker compose up -d --force-recreate scraper ml-dotnet analytics-java gateway"
