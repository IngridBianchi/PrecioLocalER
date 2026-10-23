"""Infraestructura de tests de integración contra Postgres real.

Por qué existe (deuda de tests, auditada 2026-10-01): hasta entonces
`app/storage/postgres.py` estaba al 40% de cobertura y `app/pipelines/runner.py`
al 23%, justamente las dos piezas que más escriben en la base y las que más
cambiaron con la migración 006 (modelo de variantes). Los tests de API usaban
un `FakeRepository`, así que ninguna de las queries —las dos capas de
`DISTINCT ON`, el `ON CONFLICT` del hash, el barrido de SKU inactivos— se
había ejecutado nunca bajo un motor real.

Reglas de este módulo:

1. **Nunca contra la base de desarrollo.** Se exige `TEST_DATABASE_URL` y la
   base tiene que llamarse distinto de la de la app. Si apunta a la de
   desarrollo, el módulo falla en vez de truncar los datos reales.
2. **Esquema desde las migraciones reales.** No hay DDL duplicado en los tests:
   se aplican los `.sql` de `db/supabase/migrations/` en orden, lo que además
   verifica que las migraciones siguen siendo aplicables.
3. **Skip limpio** si no hay `TEST_DATABASE_URL`: los tests unitarios y el
   `pytest` de una máquina sin Postgres siguen pasando.

Variables:
- `TEST_DATABASE_URL`: DSN de la base de test (obligatoria para correr esto).
- `TEST_DB_ADMIN_URL`: DSN de la base `postgres` para poder crear la de test.
  Si se omite, se deriva reemplazando el path de `TEST_DATABASE_URL`.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio

# <repo>/services/scraper/tests/integration/conftest.py -> parents[4] = <repo>
REPO_ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS_DIR = REPO_ROOT / "db" / "supabase" / "migrations"

# Tablas de datos que se vacían entre tests. El orden da igual: RESTART
# IDENTITY + CASCADE deja los identity en 1 y limpia las dependencias.
DATA_TABLES = (
    "price_points",
    "product_skus",
    "forecasts",
    "ml_models",
    "anomalies",
    "predictions",
    "alert_events",
    "alerts",
    "products",
)

# Bases que contienen datos reales y que este módulo jamás debe tocar.
FORBIDDEN_DB_NAMES = {"preciolocal", "postgres"}


def _require_test_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("TEST_DATABASE_URL no seteada: se omite la suite contra Postgres real")
    name = urlsplit(url).path.lstrip("/")
    if name in FORBIDDEN_DB_NAMES:
        pytest.fail(
            f"TEST_DATABASE_URL apunta a '{name}', que es la base de la aplicación. "
            "Los tests de integración truncan tablas: usá una base dedicada "
            "(ej. preciolocal_test)."
        )
    return url


def _admin_url(test_url: str) -> str:
    explicit = os.environ.get("TEST_DB_ADMIN_URL", "").strip()
    if explicit:
        return explicit
    parts = urlsplit(test_url)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))


async def _apply_migrations(pool: object) -> None:
    """Aplica las migraciones del repo en orden.

    Cada archivo trae su propio BEGIN/COMMIT, así que asyncpg los ejecuta tal
    cual y la migración 007 (roles, RLS) queda en su propia transacción.
    """
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert files, f"no se encontraron migraciones en {MIGRATIONS_DIR}"
    async with pool.acquire() as conn:  # type: ignore[attr-defined]
        for path in files:
            await conn.execute(path.read_text(encoding="utf-8"))


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_url() -> AsyncIterator[str]:
    yield _require_test_url()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pg_pool(db_url: str) -> AsyncIterator[object]:
    """Recrea la base de test desde cero, aplica el esquema y expone un pool.

    Sesión porque el esquema se aplica una sola vez; lo que cambia por test es
    solo el contenido de las tablas (ver el fixture `repository`).

    **Se tira abajo y se recrea, no solo se crea si falta.** Reaplicar las
    migraciones sobre una base con datos de una corrida anterior falló con
    UniqueViolationError en el seed de `product_skus`: los tests tienen que
    arrancar siempre de un esquema y datos limpios, si no la suite depende de
    lo que quedó de la corrida previa (y un test que falla a mitad la deja
    sucio para la siguiente). El DROP va con FORCE para no quedar trabado si
    quedó alguna conexión abierta.
    """
    import asyncpg

    admin = await asyncpg.create_pool(dsn=_admin_url(db_url), min_size=1, max_size=2)
    target = urlsplit(db_url).path.lstrip("/")
    try:
        # Identificadores interpolados a propósito: asyncpg no permite
        # parametrizar DDL. `target` viene de una URL que controla el operador
        # y ya se validó arriba contra la lista de bases reales.
        await admin.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{target}"')
    finally:
        await admin.close()

    pool = await asyncpg.create_pool(dsn=db_url, min_size=1, max_size=5)
    try:
        await _apply_migrations(pool)
        yield pool
    finally:
        await pool.close()


class _PoolShim:
    """Adaptador mínimo al contrato de `PoolProvider`.

    `ProductRepository` solo usa `.pool`; el provider real además gestiona el
    ciclo de vida del pool, que acá ya maneja pytest. Encapsularlo evita abrir
    una segunda conexión a la base de test.
    """

    def __init__(self, pool: object) -> None:
        self.pool = pool


@pytest_asyncio.fixture(loop_scope="session")
async def repository(pg_pool: object) -> AsyncIterator[object]:
    """ProductRepository real sobre la base de test, vacía en cada test.

    `scope` es function (el contenido se limpia por test) pero `loop_scope` es
    session: el pool se creó en el loop de sesión y asyncpg no tolera usarlo
    desde otro loop ("attached to a different loop"). Por eso los tests de
    este módulo se marcan con `loop_scope="session"`.
    """
    from app.storage.postgres import ProductRepository

    async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
        await conn.execute(f"TRUNCATE {', '.join(DATA_TABLES)} RESTART IDENTITY CASCADE")
    yield ProductRepository(_PoolShim(pg_pool))  # type: ignore[arg-type]


@pytest_asyncio.fixture(loop_scope="session")
async def chain_id(repository: object) -> int:
    """Cadena de test: la que siembra 002_seed.sql."""
    value = await repository.chain_id("la_anonima")  # type: ignore[attr-defined]
    assert value is not None, "002_seed.sql debería haber sembrado la_anonima"
    return value