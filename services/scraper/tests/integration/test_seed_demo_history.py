"""Integration tests de `scripts/seed_demo_history.py`: lo que escribe.

El script genera histórico sintético para poder demostrar el ML sin esperar 14
días de scraping. Justamente por eso es peligroso: si corre donde no debe, o
si `--purge` borra algo que no es suyo, ensucia la base real de precios o peor,
se lleva datos legítimos.

Lo que se verifica acá es lo que toca la base:

1. Lo que genera: productos marcados DEMO-, un precio por día y por cadena, y
   un shape de serie que los modelos puedan usar.
2. Que sea idempotente y que `--dry-run` no escriba.
3. Que `--purge` borre exactamente lo que el script crea y nada más.

Las guardas de entorno (`_guard`) y la dinámica del precio son funciones puras
y están en `tests/unit/test_seed_demo_history.py`.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.ml.series import build_series
from scripts.seed_demo_history import DEFAULT_CHAINS, DEMO_PREFIX, purge, seed

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

DAYS = 30


# ---------------------------------------------------------------------------
# seed(): lo que efectivamente escribe
# ---------------------------------------------------------------------------


async def test_seed_product_names_are_prefixed_and_normalized(
    repository: object, pg_pool: object
) -> None:
    """Guardas 1 y 2: distinguible a simple vista y por nombre normalizado."""
    await seed(repository, ["la_anonima"], DAYS, count=2, dry_run=False)

    rows = await pg_pool.fetch(
        "SELECT name, normalized_name FROM products WHERE name LIKE $1 ORDER BY id",
        f"{DEMO_PREFIX}%",
    )
    assert len(rows) == 2
    for row in rows:
        assert row["name"].startswith(DEMO_PREFIX)
        # `normalize_name` tokeniza: "DEMO-Aceite Girasol 900cc" -> "demo aceite..."
        assert row["normalized_name"].startswith("demo")


async def test_seed_creates_one_point_per_day_per_chain(
    repository: object, chain_id: int, pg_pool: object
) -> None:
    await seed(repository, ["la_anonima"], DAYS, count=1, dry_run=False)
    n = await pg_pool.fetchval(
        "SELECT count(*) FROM price_points WHERE chain_id = $1", chain_id
    )
    assert n == DAYS


async def test_seed_spreads_products_across_chains(
    repository: object, pg_pool: object
) -> None:
    """El comparativo de cadenas es el motivo de ser del script: los precios
    de partida difieren por cadena."""
    await seed(repository, ["la_anonima", "carrefour"], DAYS, count=1, dry_run=False)
    rows = await pg_pool.fetch(
        """
        SELECT c.slug, min(p.price) AS lo, max(p.price) AS hi
        FROM price_points p JOIN chains c ON c.id = p.chain_id
        GROUP BY c.slug
        """
    )
    assert len(rows) == 2
    lows = sorted(r["lo"] for r in rows)
    assert lows[0] < lows[1], "las cadenas no arrancan al mismo precio"


async def test_seed_is_idempotent(repository: object, chain_id: int, pg_pool: object) -> None:
    """Correrlo dos veces no duplica: el hash de price_points lo evita."""
    first = await seed(repository, ["la_anonima"], DAYS, count=1, dry_run=False)
    second = await seed(repository, ["la_anonima"], DAYS, count=1, dry_run=False)
    n = await pg_pool.fetchval("SELECT count(*) FROM price_points")
    assert first == DAYS
    assert second == 0, "la segunda pasada no debería insertar nada"
    assert n == DAYS


async def test_seed_output_is_modelable(repository: object, chain_id: int, pg_pool: object) -> None:
    """El punto de todo el script: que el forecast pueda correr con lo que genera."""
    await seed(repository, ["la_anonima"], DAYS, count=1, dry_run=False)
    rows = await pg_pool.fetch(
        "SELECT captured_at, price FROM price_points ORDER BY captured_at"
    )
    series = build_series(rows)
    assert len(series) == DAYS
    assert not series.is_empty
    assert all(v > 0 for v in series.values)


async def test_seed_warns_and_skips_unknown_chain(
    repository: object, capsys: pytest.CaptureFixture[str]
) -> None:
    total = await seed(repository, ["cadena_fantasma"], DAYS, count=1, dry_run=False)
    assert total == 0
    assert "no está en la DB" in capsys.readouterr().out


async def test_dry_run_writes_nothing(repository: object, pg_pool: object) -> None:
    total = await seed(repository, ["la_anonima"], DAYS, count=2, dry_run=True)
    assert total == 2 * DAYS, "cuenta lo que habría insertado"
    assert await pg_pool.fetchval("SELECT count(*) FROM products") == 0


async def test_seed_rejects_asking_for_more_products_than_exist(
    repository: object, pg_pool: object
) -> None:
    await seed(repository, ["la_anonima"], DAYS, count=99, dry_run=False)
    n = await pg_pool.fetchval("SELECT count(*) FROM products")
    assert n == 6, "DEMO_SLUGS tiene 6: pedir más no inventa productos"


# ---------------------------------------------------------------------------
# purge(): borra lo suyo y nada más
# ---------------------------------------------------------------------------


async def test_purge_removes_only_demo_products(
    repository: object, chain_id: int, pg_pool: object
) -> None:
    """Lo más importante del script: `--purge` no puede tocar un precio real."""
    from app.models import PricePointIn, ProductIn, SkuIn
    from app.pipelines.dedupe import compute_price_hash

    real_id = await repository.upsert_product(  # type: ignore[attr-defined]
        ProductIn(name="Producto Real", normalized_name="producto real")
    )
    real_sku = await repository.upsert_sku(  # type: ignore[attr-defined]
        SkuIn(product_id=real_id, chain_id=chain_id, external_id="REAL-1", name="Producto Real")
    )
    captured = datetime.now(UTC) - timedelta(hours=1)
    await repository.insert_price_point(  # type: ignore[attr-defined]
        PricePointIn(
            product_id=real_id,
            chain_id=chain_id,
            sku_id=real_sku,
            store_id=None,
            price=Decimal("1234.56"),
            captured_at=captured,
            raw_json={},
            hash=compute_price_hash(chain_id, real_sku, "1234.56", captured.isoformat()),
        )
    )

    await seed(repository, ["la_anonima"], DAYS, count=2, dry_run=False)

    removed = await purge(repository)
    assert removed == 2

    assert await pg_pool.fetchval("SELECT count(*) FROM products") == 1
    survivor = await pg_pool.fetchrow("SELECT name FROM products WHERE id = $1", real_id)
    assert survivor["name"] == "Producto Real"
    # Y el histórico real sigue ahí, con su precio.
    assert await pg_pool.fetchval("SELECT count(*) FROM price_points") == 1


async def test_purge_cleans_up_every_child_row(
    repository: object, pg_pool: object
) -> None:
    """Sin ON DELETE CASCADE, un purge a medias deja basura huérfana."""
    await seed(repository, ["la_anonima"], DAYS, count=1, dry_run=False)
    await purge(repository)
    for table in ("price_points", "product_skus", "forecasts", "ml_models", "anomalies"):
        n = await pg_pool.fetchval(f"SELECT count(*) FROM {table}")  # noqa: S608
        assert n == 0, f"quedaron filas en {table}"


async def test_purge_on_empty_db_is_a_noop(
    repository: object, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await purge(repository) == 0
    assert "no hay productos DEMO-" in capsys.readouterr().out


async def test_default_chains_are_the_seeded_ones(repository: object) -> None:
    """Si el seed por default apunta a cadenas que no existen, no hace nada."""
    for slug in DEFAULT_CHAINS:
        assert await repository.chain_id(slug) is not None, f"{slug} no está sembrada"  # type: ignore[attr-defined]
