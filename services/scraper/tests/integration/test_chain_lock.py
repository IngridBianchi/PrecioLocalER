"""El lock por cadena contra Postgres real (P0.3).

`tests/unit/test_scrape_guards.py` prueba la lógica con fakes; acá se prueba
que la premisa de la que parte todo el diseño sea cierta en Postgres:

  `pg_try_advisory_lock` es de **sesión**, no de transacción ni de statement.

Si eso fuera falso (si el lock se perdiera al commit, o si un unlock desde otra
conexión alcanzara a liberar el lock de otra), el código del endpoint pasaría
los tests unitarios y en producción dos scrapes correrían a la vez, que es
exactamente lo que el lock vino a evitar. Un fake no puede detectar esa
diferencia: necesita el servidor real.
"""
from __future__ import annotations

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _try_lock(conn: object, chain: str) -> bool:
    return bool(
        await conn.fetchval(  # type: ignore[attr-defined]
            "SELECT pg_try_advisory_lock(hashtext($1))", chain
        )
    )


async def test_lock_sobrevive_a_un_commit_entre_scrapes(pg_pool: object) -> None:
    """El lock NO se suelta al hacer commit: es de sesión.

    Un runner real hace cientos de commits (un upsert por producto). Si el lock
    fuera de transacción, el segundo scrape entraría por el hueco. Este test
    replica esa secuencia exacta: acquire → commit → try de otra sesión.
    """
    async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
        chain = f"test-commit-{id(conn)}"
        assert await _try_lock(conn, chain) is True

        # Commit explícito, en la misma sesión: es el punto del test.
        await conn.execute("COMMIT")  # type: ignore[attr-defined]

        # Sesión distinta: es la que representa el segundo scrape.
        async with pg_pool.acquire() as otra:  # type: ignore[attr-defined]
            assert await _try_lock(otra, chain) is False, (
                "el commit liberó el lock: el endpoint permitiría dos scrapes "
                "solapados justo en el medio de un scrape real"
            )


async def test_unlock_desde_otra_sesion_no_libera(pg_pool: object) -> None:
    """La sesión que NO tomó el lock no puede soltarlo.

    Es el error que evita `pool.fetchval(...)` (que toma y suelta una conexión
    por query): el acquire saldría por un socket y el unlock por otro, el
    `pg_advisory_unlock` no encontraría nada, devolvería false y el lock
    seguiría tomado en la sesión muerta hasta que el pool la cerrara.
    """
    async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
        chain = f"test-otra-sesion-{id(conn)}"
        assert await _try_lock(conn, chain) is True

        async with pg_pool.acquire() as otra:  # type: ignore[attr-defined]
            liberado = await otra.fetchval(  # type: ignore[attr-defined]
                "SELECT pg_advisory_unlock(hashtext($1))", chain
            )
            assert liberado is False, "otra sesión liberó un lock que no era suyo"

        # El lock sigue siendo de `conn`: nadie más lo tiene.
        async with pg_pool.acquire() as tercera:  # type: ignore[attr-defined]
            assert await _try_lock(tercera, chain) is False


async def test_unlock_en_la_misma_sesion_libera(pg_pool: object) -> None:
    """Y el camino normal sí libera, así la cadena no queda trabada.

    Si este test falla, el endpoint está dejando la cadena con el lock tomado
    para siempre después de cada scrape: el segundo scrape da 409 sin importar
    que el primero haya terminado.
    """
    async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
        chain = f"test-release-{id(conn)}"
        assert await _try_lock(conn, chain) is True
        liberado = await conn.fetchval(  # type: ignore[attr-defined]
            "SELECT pg_advisory_unlock(hashtext($1))", chain
        )
        assert liberado is True

    async with pg_pool.acquire() as otra:  # type: ignore[attr-defined]
        assert await _try_lock(otra, chain) is True
        await otra.execute("SELECT pg_advisory_unlock(hashtext($1))", chain)  # type: ignore[attr-defined]


async def test_lock_se_libera_solo_si_la_conexion_muere(
    pg_pool: object, db_url: str
) -> None:
    """Si el contenedor muere a mitad de scrape, la cadena no queda trabada.

    Es el argumento para preferir Postgres a un lock en memoria: si el proceso
    se muere (OOM, deploy, Ctrl-C), la sesión cae y Postgres libera el lock
    solo.

    Usa una conexión propia y no una del pool a propósito: al salir de
    `pool.acquire()` la conexión vuelve al pool y sigue viva en el servidor, así
    que con el pool no se puede simular una muerte — la conexión nunca muere.
    """
    conn = await asyncpg.connect(db_url)
    try:
        chain = "test-muerte-de-conexion"
        assert await _try_lock(conn, chain) is True
    finally:
        await conn.close()

    async with pg_pool.acquire() as otra:  # type: ignore[attr-defined]
        assert await _try_lock(otra, chain) is True, (
            "el lock sobrevivió a la muerte de la conexión: quedaría trabada "
            "la cadena hasta un restart manual de Postgres"
        )
        await otra.execute("SELECT pg_advisory_unlock(hashtext($1))", chain)  # type: ignore[attr-defined]


async def test_lock_es_por_cadena_y_no_global(pg_pool: object) -> None:
    """Un scrape de Coto no bloquea uno de La Anónima.

    El lock es por slug a propósito: si fuera global, con 5 cadenas y un scrape
    de un minuto cada una, el tráfico se serializa entero y el 5/hora por cadena
    deja de ser cierto. Es también la razón de hashear el slug y no usar un id
    fijo.
    """
    async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
        assert await _try_lock(conn, "la_anonima") is True

        async with pg_pool.acquire() as otra:  # type: ignore[attr-defined]
            assert await _try_lock(otra, "coto") is True, "cadenas distintas se bloquean entre sí"
            await otra.execute("SELECT pg_advisory_unlock(hashtext($1))", "coto")  # type: ignore[attr-defined]

        await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", "la_anonima")  # type: ignore[attr-defined]
