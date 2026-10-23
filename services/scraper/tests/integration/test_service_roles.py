"""El contrato de P0.1: la RLS tiene que separar, no acompañar.

Antes de 011_service_roles.sql todo esto era decorativo, por dos motivos
distintos, y este archivo existe para que los dos dejen de serlo:

  1. La app conectaba como `preciolocal`, que es superusuario del contenedor de
     Postgres, y un superusuario tiene `rolbypassrls`: se saltea la RLS
     entera. Da igual cuántas policies haya ni si la tabla tiene FORCE.
  2. Las policies comparaban `current_user = 'pl_scraper'`, y `current_user` es
     el rol con el que te conectás, no el del que heredás permisos. El rol
     LOGIN es miembro de `pl_scraper`, no `pl_scraper`, así que la comparación
     daba false y hasta el scraper legítimo era rechazado con
     `new row violates row-level security policy`.

El diseño del test es lo que hace que sea una prueba y no una constatación:
**los dos roles efímeros reciben exactamente los mismos GRANTs.** Los dos
resuelven `price_points`, los dos pueden `SELECT` e `INSERT`. Lo único que
difiere es la membresía al grupo `pl_scraper`. Si el outsider pudiera insertar,
el separador real serían los GRANTs y no la policy — o sea, la RLS seguiría
siendo decorativa, que es el bug que había.

Por eso los roles se crean acá y no se leen de `.env`: el suite tiene que
correr también contra el Postgres efímero de CI, donde no existe ningún rol de
servicio configurado.

Requiere que el usuario del `TEST_DATABASE_URL` pueda crear roles: en CI es
superusuario del `postgres:16-alpine`; localmente hay que darle `CREATEROLE` a
`preciolocal_test` (ver docs/testing.md).
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest
import pytest_asyncio

# Los roles son de cluster, no de base: el sufijo único evita colisionar con
# una corrida anterior que haya fallado antes del cleanup.
SUFFIX = uuid.uuid4().hex[:8]
SERVICE_ROLE = f"pl_t_service_{SUFFIX}"
OUTSIDER_ROLE = f"pl_t_outsider_{SUFFIX}"
PASSWORD = uuid.uuid4().hex

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

# `hash` es NOT NULL UNIQUE y `raw_json` también NOT NULL: se completan todos
# los NOT NULL para que al rol de servicio no le salte un error de datos en
# lugar del resultado que el test quiere medir. El hash se deriva de random()
# para que dos inserts del mismo producto no choquen.
INSERT_PRICE = """
INSERT INTO price_points (product_id, chain_id, price, captured_at, raw_json, hash)
SELECT id,
       (SELECT id FROM chains LIMIT 1),
       12.34,
       now(),
       '{}'::jsonb,
       md5(random()::text || clock_timestamp()::text)
FROM products LIMIT 1
RETURNING id
"""


def _dsn(db_url: str, role: str) -> str:
    """DSN del rol efímero. La password es de test y no es un secreto."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(db_url)
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit(
        (parts.scheme, f"{role}:{PASSWORD}@{host}{port}", parts.path, parts.query, "")
    )


async def _fetch(db_url: str, role: str, sql: str) -> list[str]:
    conn = await asyncpg.connect(dsn=_dsn(db_url, role))
    try:
        return [row[0] for row in await conn.fetch(sql)]
    finally:
        await conn.close()


async def _admin(db_url: str) -> asyncpg.Connection:
    """Conexión administrativa **a la base de test**, no a la de `postgres`.

    Los GRANT son por base: correrlos desde `/postgres` los aplicaría al
    schema public de esa base, que está vacío, y fallarían con
    `relation "price_points" does not exist`. El `CREATE ROLE` es de cluster
    y funciona igual desde acá.
    """
    return await asyncpg.connect(dsn=db_url)


async def _drop_role(admin: asyncpg.Connection, role: str) -> None:
    """Borra un rol efímero y todo lo que le quedó en esta base. **Verifica.**

    Hay que revocar antes de dropear, por motivos distintos:

      - `DROP ROLE` falla con DependentObjectsStillExistError si el rol tiene
        privilegios sobre tablas, sequences o funciones.
      - También falla si **alguien es miembro de él**, porque la fila de
        `pg_auth_members` es una dependencia del rol. Y ese caso no es
        hipotético: desde Postgres 16, un rol con CREATEROLE recibe
        automáticamente ADMIN OPTION sobre los roles que crea, así que el propio
        `preciolocal_test` queda como miembro del rol efímero. Sin el `REVOKE`
        de abajo, el `DROP` falla siempre.
      - `DROP OWNED BY` (que resolvería lo primero) exige ser miembro del rol o
        superusuario, y el rol de test no es miembro del suyo.

    El borrado **no tolera fallos en silencio**. Una versión anterior de esta
    función se tragaba todos los `PostgresError` con un `continue` y por eso
    dejó doce roles huérfanos por corrida en el cluster de desarrollo, sin que
    ningún test se enterara: el `DROP` fallaba, el error se descartaba y la
    suite pasaba en verde. Acá cada REVOKE que se puede skipear se skipea a
    propósito (revocar algo que nunca se otrorgó es ruido), pero al final se
    **afirma** que el rol no existe más.
    """
    if not await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role):
        return

    # Primero las membresías ajenas: es la dependencia que rompe el DROP y la
    # única que no se resuelve con un REVOKE de privilegios del rol.
    miembros = await admin.fetch(
        "SELECT r.rolname FROM pg_auth_members m "
        "JOIN pg_roles r ON r.oid = m.member "
        "WHERE m.roleid = (SELECT oid FROM pg_roles WHERE rolname = $1)",
        role,
    )
    for fila in miembros:
        try:
            await admin.execute(f'REVOKE "{role}" FROM "{fila["rolname"]}"')
        except asyncpg.PostgresError:
            continue

    for sql in (
        f'REVOKE pl_scraper, pl_app, pl_ml FROM "{role}"',
        f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM "{role}"',
        f'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM "{role}"',
        # Las funciones también: sin esto, el GRANT EXECUTE sobre
        # pl_current_user_id() deja una dependencia y el DROP falla con
        # DependentObjectsStillExistError.
        f'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM "{role}"',
        f'REVOKE ALL PRIVILEGES ON SCHEMA public FROM "{role}"',
        f'DROP ROLE IF EXISTS "{role}"',
    ):
        try:
            await admin.execute(sql)
        except asyncpg.PostgresError as exc:
            raise AssertionError(
                f"no se pudo limpiar el rol efímero {role!r} con: {sql!r} ({exc}). "
                "Si queda vivo contamina el resto de la suite y el cluster: "
                "no seguir, arreglar el cleanup."
            ) from exc

    assert not await admin.fetchval(
        "SELECT 1 FROM pg_roles WHERE rolname = $1", role
    ), f"el rol efímero {role!r} sigue existiendo después del cleanup"


@pytest_asyncio.fixture(loop_scope="session")
async def service_and_outsider(db_url: str, pg_pool: object) -> None:
    """Crea los dos roles: GRANTs idénticos, membresía distinta.

    Depende de `pg_pool` (aunque no lo use) por una razón concreta: ese fixture
    es el que tira abajo, recrea y migra la base. Sin esa dependencia, los GRANTs
    correrían contra un esquema inexistente y fallarían con
    `relation "price_points" does not exist`.

    Se le presta SELECT sobre `users` a los dos, aunque `pl_scraper` real no lo
    tiene (007 se lo da solo a `pl_app`, y con razón: el scraper no gestiona
    usuarios). Es un préstamo de test, para poder ejercitar `users_self_select`.
    """
    admin = await _admin(db_url)
    try:
        # `CREATE ROLE` es una utility statement: no acepta parámetros ($1),
        # el server no llega a parsearla como prepared statement. Como la
        # password es un hex generado por este mismo módulo, el literal es
        # seguro; el assert deja el contrato explícito igual.
        assert PASSWORD.isalnum(), f"password de test no segura: {PASSWORD!r}"
        for role in (SERVICE_ROLE, OUTSIDER_ROLE):
            # Un DROP previo no es paranoia: los roles son de cluster, así que
            # una corrida abortada a mitad los deja vivos y la siguiente falla
            # con DuplicateObjectError. Es el mismo motivo por el que el
            # conftest tira abajo y recrea la base.
            await _drop_role(admin, role)
            await admin.execute(
                f'CREATE ROLE "{role}" LOGIN INHERIT NOSUPERUSER NOBYPASSRLS '
                f"PASSWORD '{PASSWORD}'"
            )
        await admin.execute(f'GRANT pl_scraper TO "{SERVICE_ROLE}"')
        await admin.execute(
            f'GRANT USAGE ON SCHEMA public TO "{SERVICE_ROLE}", "{OUTSIDER_ROLE}"; '
            f"GRANT SELECT, INSERT ON price_points TO {SERVICE_ROLE}, {OUTSIDER_ROLE}; "
            f"GRANT SELECT ON users TO {SERVICE_ROLE}, {OUTSIDER_ROLE}; "
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
            f"TO {SERVICE_ROLE}, {OUTSIDER_ROLE}; "
            # El helper de la policy también es un privilege: 007 le saca el
            # EXECUTE al PUBLIC y solo se lo da a pl_scraper/pl_app. Sin esto,
            # evaluar `users_self_select` falla con
            # "permission denied for function pl_current_user_id" antes de
            # llegar a comparar nada.
            f"GRANT EXECUTE ON FUNCTION pl_current_user_id() "
            f"TO {SERVICE_ROLE}, {OUTSIDER_ROLE}"
        )
        # Los INSERT de price_points necesitan un product_id que exista, y el
        # conftest trunca `products` entre tests. Se siembra uno con el nombre
        # único de la corrida para no chocar con el unique de 004.
        await admin.execute(
            """
            INSERT INTO products (name, normalized_name)
            VALUES ($1, $1)
            ON CONFLICT (normalized_name) DO NOTHING
            """,
            f"Producto para RLS {SUFFIX}",
        )
    finally:
        await admin.close()

    try:
        yield
    finally:
        admin = await _admin(db_url)
        try:
            for role in (SERVICE_ROLE, OUTSIDER_ROLE):
                await _drop_role(admin, role)
            # Se revisa **todo** lo que huela a rol efímero, no solo los de
            # esta corrida: si una corrida anterior se abortó a mitad, su
            # basura sigue en el cluster y hay que saberlo acá. Los roles son
            # de cluster, así que un huérfano de la corrida pasada contamina
            # esta igual.
            #
            # La corrida abortada (Ctrl-C, un `--maxfail`, un OOM) no pasa por
            # este teardown, así que esto no las cubre: por eso el prefijo
            # `pl_t_` se usa en el `DROP` previo al `CREATE` y no al revés.
            huerfanos = [
                fila["rolname"]
                for fila in await admin.fetch(
                    "SELECT rolname FROM pg_roles WHERE rolname LIKE 'pl\\_t\\_%' ORDER BY 1"
                )
            ]
            assert not huerfanos, (
                f"quedaron roles de test sin borrar en el cluster: {huerfanos}. "
                "Un rol vivo por corrida termina llenando pg_roles y hace que "
                "los CREATE fallen con DuplicateObjectError. Borrarlos a mano "
                "o revisar el cleanup del fixture."
            )
        finally:
            await admin.close()


async def test_rol_de_servicio_puede_insertar(
    db_url: str, service_and_outsider: None
) -> None:
    """El scraper legítimo escribe: la membresía alcanza, sin `SET ROLE`.

    Este es el test que 007/008/010 no podían tener. Con la policy anterior
    fallaba aun con la membresía puesta, porque `current_user` era el rol LOGIN
    y no `pl_scraper`.
    """
    rows = await _fetch(db_url, SERVICE_ROLE, INSERT_PRICE)
    assert rows, "el rol de servicio debería poder insertar en price_points"


async def test_rol_sin_membresia_no_puede_insertar(
    db_url: str, service_and_outsider: None
) -> None:
    """Con los MISMOS GRANTs pero sin membresía, la escritura se rechaza.

    El outsider tiene `INSERT` sobre la tabla, así que lo único que puede
    frenarlo es `price_points_service_write` (FOR INSERT WITH CHECK
    pl_is_service('pl_scraper')). Si este test dejara pasar la escritura, la
    policy no estaría protegiendo nada.
    """
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _fetch(db_url, OUTSIDER_ROLE, INSERT_PRICE)


async def test_lectura_de_precios_es_publica_para_ambos(
    db_url: str, service_and_outsider: None
) -> None:
    """`price_points_public_read` es `USING (true)`: los dos leen igual.

    Leer precios es público por diseño —es un comparador de precios, no un
    depósito de archivos— y la RLS protege escrituras y datos de usuario. Que
    los dos roles lean lo mismo no es un agujero: es el comportamiento
    esperado, y el test lo fija para que nadie lo "corrija" después.
    """
    antes = int((await _fetch(db_url, SERVICE_ROLE, "SELECT count(*) FROM price_points"))[0])
    await _fetch(db_url, SERVICE_ROLE, INSERT_PRICE)
    leido_por_el_outsider = int(
        (await _fetch(db_url, OUTSIDER_ROLE, "SELECT count(*) FROM price_points"))[0]
    )
    assert leido_por_el_outsider == antes + 1


async def test_usuarios_sin_guc_devuelve_cero(
    db_url: str, service_and_outsider: None
) -> None:
    """`users_self_select` es deny-by-default si no está `app.user_id`.

    Es la propiedad de aislamiento por usuario: sin el GUC que setea la capa de
    conexión al resolver el JWT, `pl_current_user_id()` devuelve NULL, la policy
    no matchea y no se ve ninguna fila. Con el superusuario esto daba igual
    (se saltaba todo); con un rol normal, no.
    """
    for role in (SERVICE_ROLE, OUTSIDER_ROLE):
        rows = await _fetch(db_url, role, "SELECT count(*) FROM users")
        assert rows == [0], f"{role} sin app.user_id no debería ver usuarios: {rows}"


async def test_con_su_guc_cada_rol_ve_solo_su_usuario(
    db_url: str, service_and_outsider: None
) -> None:
    """Con `app.user_id` seteado, cada conexión ve exactamente un usuario.

    Cierra el círculo: las policies de usuario filtran por el GUC, no por el
    rol. El aislamiento entre usuarios depende de que la capa de conexión setee
    `SET LOCAL app.user_id` dentro de la transacción; este test fija esa
    expectativa del lado de la base.
    """
    admin = await _admin(db_url)
    try:
        # `users` tiene FORCE RLS y ninguna policy de INSERT, así que ni
        # siquiera el owner puede sembrarla: el FORCE existe justamente para
        # que el dueño de la tabla tampoco esté por encima de las reglas. Por
        # eso el seed afloja el FORCE (NO FORCE alcanza para que el owner
        # vuelva a estar por encima) y lo restaura en el finally.
        await admin.execute("ALTER TABLE users NO FORCE ROW LEVEL SECURITY")
        try:
            await admin.execute(
                """
                INSERT INTO users (email) VALUES ($1), ($2)
                ON CONFLICT (email) DO NOTHING
                """,
                f"rls-{SUFFIX}-a@test.invalid",
                f"rls-{SUFFIX}-b@test.invalid",
            )
            ids = await admin.fetch(
                "SELECT id FROM users WHERE email LIKE $1 ORDER BY email",
                f"rls-{SUFFIX}-%",
            )
        finally:
            await admin.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")

        assert len(ids) == 2, f"el seed de usuarios de test falló: {ids}"
        id_a, id_b = ids[0][0], ids[1][0]

        # La property que importa no es "el rol ve a su dueño", sino que la
        # visibilidad la decide **el GUC y nada más**: ante cualquier GUC
        # válido, la conexión ve exactamente esa fila y ni una más. Se prueba
        # con los dos roles y los dos usuarios cruzados (4 combinaciones),
        # porque una matriz de 1x1 no distingue "filtra por GUC" de
        # "devuelve siempre la primera fila".
        for role in (SERVICE_ROLE, OUTSIDER_ROLE):
            for guc in (id_a, id_b):
                filas = await _visibles(db_url, role, str(guc))
                assert filas == [str(guc)], (
                    f"{role} con app.user_id={guc} debería ver solo esa fila, "
                    f"vio {filas}"
                )
    finally:
        # La limpieza también necesita el FORCE flojo: `users` no tiene policy de
        # DELETE, así que con RLS activa nadie borra ni su propia fila.
        await admin.execute("ALTER TABLE users NO FORCE ROW LEVEL SECURITY")
        try:
            await admin.execute(
                "DELETE FROM users WHERE email LIKE $1", f"rls-{SUFFIX}-%"
            )
        finally:
            await admin.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
            await admin.close()


async def _visibles(db_url: str, role: str, user_id: str) -> list[str]:
    """Filas de `users` que ve `role` con `app.user_id = user_id`.

    El GUC es de sesión y `SET LOCAL` es de transacción, así que tiene que ir
    en la misma transacción que el SELECT (no se puede setear en una conexión
    y consultar en otra).

    Va por `set_config` y no por `SET LOCAL app.user_id = $1` porque `SET` es
    una utility statement y no acepta parámetros: asyncpg lo rechaza con
    `syntax error at or near "$1"`, que no dice nada del origen real.
    """
    conn = await asyncpg.connect(dsn=_dsn(db_url, role))
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', $1, true)", user_id)
            return [row[0] for row in await conn.fetch("SELECT id::text FROM users")]
    finally:
        await conn.close()
