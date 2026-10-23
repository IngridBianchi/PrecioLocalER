"""Integration tests de `ProductRepository` contra Postgres real.

Cubre lo que ningún test podía cubrir antes: el SQL de verdad. Cada assertion
que falla acá es un bug de producción, no un mock desalineado.

Ejecutar:
    TEST_DATABASE_URL=postgresql://user:pass@127.0.0.1:5432/preciolocal_test \\
      python -m pytest tests/integration/test_repository_postgres.py

Sin esa variable el módulo se omite (ver conftest.py).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest
from app.models import PricePointIn, ProductIn, SkuIn
from app.pipelines.dedupe import compute_price_hash
from app.pipelines.normalize import normalize_name as _normalize

# `loop_scope="session"`: el pool de Postgres del conftest vive en el loop de
# sesión (asyncpg no permite compartir conexiones entre loops distintos).
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

# Los CHECK de 008_ml.sql (model y language) se ejercitan contra el motor real:
# asyncpg tipa el error, así que los tests no necesitan un catches-all.

BASE_DAY = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers: construyen el escenario de una cadena con N SKU por producto.
# ---------------------------------------------------------------------------


async def _make_product(repo: object, name: str) -> int:
    product_id = await repo.upsert_product(  # type: ignore[attr-defined]
        ProductIn(name=name, normalized_name=_normalize(name))
    )
    assert product_id is not None
    return product_id


async def _make_sku(
    repo: object, *, product_id: int, chain_id: int, external_id: str, name: str
) -> int:
    sku_id = await repo.upsert_sku(  # type: ignore[attr-defined]
        SkuIn(
            product_id=product_id,
            chain_id=chain_id,
            external_id=external_id,
            name=name,
            url=f"https://example.test/p/{external_id}",
        )
    )
    assert sku_id is not None
    return sku_id


async def _make_point(
    repo: object,
    *,
    product_id: int,
    chain_id: int,
    sku_id: int,
    price: str,
    captured_at: datetime,
    promo_type: str | None = None,
) -> tuple[bool, int]:
    return await repo.insert_price_point(  # type: ignore[attr-defined]
        PricePointIn(
            product_id=product_id,
            chain_id=chain_id,
            sku_id=sku_id,
            store_id=None,
            price=Decimal(price),
            promo_type=promo_type,
            captured_at=captured_at,
            raw_json={"external_id": str(sku_id)},
            hash=compute_price_hash(chain_id, sku_id, price, captured_at.isoformat()),
        )
    )


# ---------------------------------------------------------------------------
# chains / chain_id
# ---------------------------------------------------------------------------


async def test_chain_id_returns_id_for_seeded_chain(
    repository: object, chain_id: int
) -> None:
    assert await repository.chain_id("la_anonima") == chain_id  # type: ignore[attr-defined]


async def test_chain_id_unknown_slug_is_none(repository: object) -> None:
    assert await repository.chain_id("no_existe") is None  # type: ignore[attr-defined]


async def test_chain_id_inactive_chain_is_hidden(repository: object, pg_pool: object) -> None:
    """Una cadena dada de baja desaparece de `chain_id` y de `chains`."""
    async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
        await conn.execute("UPDATE chains SET active = false WHERE slug = 'carrefour'")
    try:
        assert await repository.chain_id("carrefour") is None  # type: ignore[attr-defined]
        slugs = [c.slug for c in await repository.chains()]  # type: ignore[attr-defined]
        assert "carrefour" not in slugs
    finally:
        async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
            await conn.execute("UPDATE chains SET active = true WHERE slug = 'carrefour'")


async def test_chains_lists_active_ordered_by_name(repository: object) -> None:
    chains = await repository.chains()  # type: ignore[attr-defined]
    slugs = [c.slug for c in chains]
    assert "la_anonima" in slugs and "coto_digital" in slugs
    assert [c.name for c in chains] == sorted(c.name for c in chains)


# ---------------------------------------------------------------------------
# upsert_product / upsert_sku
# ---------------------------------------------------------------------------


async def test_upsert_product_is_idempotent_by_normalized_name(repository: object) -> None:
    first = await _make_product(repository, "Aceite de Girasol 900 cc")
    second = await _make_product(repository, "ACEITE de girasol 900 CC")
    assert first == second


async def test_upsert_product_updates_display_name_for_the_same_key(
    repository: object,
) -> None:
    """Recorrido nuevo de la misma clave canónica actualiza la fila, no duplica.

    La clave de dedupe es `normalized_name`, o sea que cambia la forma visible
    del producto pero no la secuencia de tokens: es el caso de una cadena que
    corrige mayúsculas o agrega un punto ("1 Kg." -> "1 kg"). El nombre nuevo
    tiene que ganar.

    `normalized_name` se deriva con `normalize_name` y no hardcodeado: la forma
    canónica es detalle de `normalize.py`, y fijarla a mano en el test lo rompe
    apenas cambie el parser (pasó al empezar a parsear "1 kg" sin la "x").
    """
    original = "Harina de Trigo 000 1 kg"
    product_id = await _make_product(repository, original)
    restyled = "Harina de Trigo 000 1 Kg."
    assert _normalize(restyled) == _normalize(original), "el caso solo vale si la clave no cambia"

    await repository.upsert_product(  # type: ignore[attr-defined]
        ProductIn(
            name=restyled,
            normalized_name=_normalize(restyled),
            size_quantity=Decimal("1"),
            size_unit="kg",
        )
    )
    detail = await repository.product_detail(product_id)  # type: ignore[attr-defined]
    assert detail is not None
    assert detail.name == restyled
    assert detail.size_unit == "kg"
    assert detail.size_quantity == Decimal("1")


async def test_upsert_product_refreshes_every_presentation_column(
    repository: object,
) -> None:
    """El DO UPDATE escribe toda la presentación, `size_unit` incluida.

    Antes el `ON CONFLICT` refrescaba `name` y `size_quantity` pero no
    `size_unit`, así que la fila podía quedar mostrando la unidad del primer
    scrape con la cantidad del último: un producto a medio actualizar. Por el
    pipeline actual es difícil de reproducir —`size_unit` es función de
    `normalized_name`, así que una unidad distinta suele ser otro producto—,
    pero el contrato del repositorio es que la fila refleje la última entrada,
    y este test lo fija. Si algún día se separa el parseo de unidad del nombre
    canónico, la garantía ya está escrita.
    """
    product_id = await _make_product(repository, "Galletitas Surtidas")
    detail = await repository.product_detail(product_id)  # type: ignore[attr-defined]
    assert detail is not None
    assert detail.size_unit == "und"

    await repository.upsert_product(  # type: ignore[attr-defined]
        ProductIn(
            name="Galletitas Surtidas",
            normalized_name=_normalize("Galletitas Surtidas"),
            size_quantity=Decimal("400"),
            size_unit="g",
        )
    )
    detail = await repository.product_detail(product_id)  # type: ignore[attr-defined]
    assert detail is not None
    assert detail.size_unit == "g"
    assert detail.size_quantity == Decimal("400")


async def test_upsert_sku_returns_same_id_per_chain_external_id(
    repository: object, chain_id: int
) -> None:
    product_id = await _make_product(repository, "Harina Chacabuco 750 gr")
    first = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="531487", name="A"
    )
    second = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="531487", name="B"
    )
    assert first == second


async def test_upsert_sku_distinct_external_ids_are_distinct_skus(
    repository: object, chain_id: int
) -> None:
    """Regresión del incidente 2026-09-24: mismo nombre, dos SKU, dos precios."""
    product_id = await _make_product(repository, "Harina De Trigo Chacabuco Paquete 750 Gr")
    a = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="531487", name="X"
    )
    b = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="531488", name="X"
    )
    assert a != b


async def test_upsert_sku_reactivates_a_previously_deactivated_sku(
    repository: object, chain_id: int
) -> None:
    """Si el SKU vuelve a la góndola, el scrape lo deja activo otra vez."""
    product_id = await _make_product(repository, "Yerba Mate 1 kg")
    sku_id = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="Y1", name="Y"
    )
    await repository.deactivate_missing_skus(  # type: ignore[attr-defined]
        chain_id, datetime.now(UTC)
    )
    again = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="Y1", name="Y"
    )
    assert again == sku_id
    rows = await repository._provider.pool.fetch(  # type: ignore[attr-defined]
        "SELECT active FROM product_skus WHERE id = $1", sku_id
    )
    assert rows[0]["active"] is True


# ---------------------------------------------------------------------------
# insert_price_point
# ---------------------------------------------------------------------------


async def test_insert_price_point_is_idempotent_by_hash(
    repository: object, chain_id: int
) -> None:
    product_id = await _make_product(repository, "Aceite 900 cc")
    sku_id = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="A1", name="A"
    )
    first = await _make_point(
        repository,
        product_id=product_id,
        chain_id=chain_id,
        sku_id=sku_id,
        price="2129.00",
        captured_at=BASE_DAY,
    )
    second = await _make_point(
        repository,
        product_id=product_id,
        chain_id=chain_id,
        sku_id=sku_id,
        price="2129.00",
        captured_at=BASE_DAY,
    )
    assert first == (True, first[1])
    assert second == (False, 0)


async def test_price_hash_distinguishes_skus_of_same_chain(
    repository: object, chain_id: int
) -> None:
    """Regresión del data loss: dos SKU con el MISMO precio y timestamp."""
    product_id = await _make_product(repository, "Harina Chacabuco 750 gr")
    a = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="531487", name="X"
    )
    b = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="531488", name="X"
    )
    inserted_a = await _make_point(
        repository,
        product_id=product_id,
        chain_id=chain_id,
        sku_id=a,
        price="2129.00",
        captured_at=BASE_DAY,
    )
    inserted_b = await _make_point(
        repository,
        product_id=product_id,
        chain_id=chain_id,
        sku_id=b,
        price="2129.00",
        captured_at=BASE_DAY,
    )
    assert inserted_a[0] is True
    assert inserted_b[0] is True, "el segundo SKU no debe descartarse por colisión de hash"


# ---------------------------------------------------------------------------
# search_products — la regla "desde $X" (mínimo por producto/cadena)
# ---------------------------------------------------------------------------


async def test_search_returns_min_price_across_skus_of_the_chain(
    repository: object, chain_id: int
) -> None:
    """Regla del incidente 2026-09-24: representativo = MENOR precio vigente."""
    product_id = await _make_product(repository, "Harina De Trigo Chacabuco Paquete 750 Gr")
    cheap = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="531487", name="X"
    )
    pricey = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="531488", name="X"
    )
    await _make_point(
        repository,
        product_id=product_id,
        chain_id=chain_id,
        sku_id=cheap,
        price="2129.00",
        captured_at=BASE_DAY,
    )
    await _make_point(
        repository,
        product_id=product_id,
        chain_id=chain_id,
        sku_id=pricey,
        price="2899.00",
        captured_at=BASE_DAY,
    )
    page = await repository.search_products("chacabuco", None)  # type: ignore[attr-defined]
    assert page.total == 1
    assert len(page.products) == 1
    assert page.products[0].price == "2129.00"
    assert page.products[0].chain == "la_anonima"


async def test_search_is_deterministic_when_prices_tie(
    repository: object, chain_id: int
) -> None:
    """Empate exacto de precio y timestamp: el orden no puede depender del plan."""
    product_id = await _make_product(repository, "Empatado 500 g")
    a = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="E1", name="E"
    )
    b = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="E2", name="E"
    )
    for sku in (a, b):
        await _make_point(
            repository,
            product_id=product_id,
            chain_id=chain_id,
            sku_id=sku,
            price="1000.00",
            captured_at=BASE_DAY,
        )
    results = []
    for _ in range(5):
        page = await repository.search_products("empatado", None)  # type: ignore[attr-defined]
        results.append([(r.id, r.price, r.captured_at) for r in page.products])
    assert all(r == results[0] for r in results)


async def test_search_picks_latest_capture_before_comparing_prices(
    repository: object, chain_id: int
) -> None:
    """`DISTINCT ON` primera capa: último point por SKU, no el más barato histórico."""
    product_id = await _make_product(repository, "Gaseosa 2 l")
    sku_id = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="G1", name="G"
    )
    await _make_point(
        repository,
        product_id=product_id,
        chain_id=chain_id,
        sku_id=sku_id,
        price="1500.00",
        captured_at=BASE_DAY,
    )
    await _make_point(
        repository,
        product_id=product_id,
        chain_id=chain_id,
        sku_id=sku_id,
        price="2500.00",
        captured_at=BASE_DAY + timedelta(days=1),
    )
    page = await repository.search_products("gaseosa", None)  # type: ignore[attr-defined]
    assert page.products[0].price == "2500.00"


async def test_search_filters_by_chain(repository: object, pg_pool: object) -> None:
    product_id = await _make_product(repository, "Filtro Cadena 1 l")
    la_anonima = await repository.chain_id("la_anonima")  # type: ignore[attr-defined]
    coto = await repository.chain_id("coto_digital")  # type: ignore[attr-defined]
    assert la_anonima is not None and coto is not None
    for chain, ext, price in ((la_anonima, "L1", "100.00"), (coto, "C1", "200.00")):
        sku = await _make_sku(
            repository, product_id=product_id, chain_id=chain, external_id=ext, name="F"
        )
        await _make_point(
            repository,
            product_id=product_id,
            chain_id=chain,
            sku_id=sku,
            price=price,
            captured_at=BASE_DAY,
        )
    all_page = await repository.search_products("filtro cadena", None)  # type: ignore[attr-defined]
    assert {r.chain for r in all_page.products} == {"la_anonima", "coto_digital"}
    assert all_page.total == 2
    only_coto_page = await repository.search_products(  # type: ignore[attr-defined]
        "filtro cadena", "coto_digital"
    )
    assert only_coto_page.total == 1
    assert len(only_coto_page.products) == 1
    assert only_coto_page.products[0].chain == "coto_digital"
    assert only_coto_page.products[0].price == "200.00"


async def test_search_excludes_deactivated_skus(
    repository: object, chain_id: int
) -> None:
    """Un SKU dado de baja sale de la búsqueda pero conserva su histórico."""
    product_id = await _make_product(repository, "Descontinuado 1 kg")
    sku_id = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="D1", name="D"
    )
    # Dentro del span por defecto (30d) de price_history, a propósito: el
    # barrido no debe sacar el punto del histórico.
    recent = datetime.now(UTC) - timedelta(days=1)
    await _make_point(
        repository,
        product_id=product_id,
        chain_id=chain_id,
        sku_id=sku_id,
        price="999.00",
        captured_at=recent,
    )
    assert len((await repository.search_products("descontinuado", None)).products) == 1  # type: ignore[attr-defined]

    await repository.deactivate_missing_skus(chain_id, datetime.now(UTC))  # type: ignore[attr-defined]

    after_deactivation = await repository.search_products("descontinuado", None)  # type: ignore[attr-defined]
    assert after_deactivation.products == []
    assert after_deactivation.total == 0
    history = await repository.price_history(product_id, None)  # type: ignore[attr-defined]
    assert len(history) == 1, "el histórico no se borra al dar de baja el SKU"
    assert history[0].sku_id == sku_id


async def test_search_respects_limit(repository: object, chain_id: int) -> None:
    for index in range(5):
        product_id = await _make_product(repository, f"Limite Producto {index}")
        sku_id = await _make_sku(
            repository, product_id=product_id, chain_id=chain_id, external_id=f"L{index}", name="L"
        )
        await _make_point(
            repository,
            product_id=product_id,
            chain_id=chain_id,
            sku_id=sku_id,
            price=f"{100 + index}.00",
            captured_at=BASE_DAY,
        )
    page = await repository.search_products("limite producto", None, limit=3)  # type: ignore[attr-defined]
    assert len(page.products) == 3
    assert page.total == 5
    second_page = await repository.search_products(  # type: ignore[attr-defined]
        "limite producto", None, limit=3, offset=3
    )
    assert len(second_page.products) == 2
    assert {r.id for r in page.products}.isdisjoint({r.id for r in second_page.products})


# ---------------------------------------------------------------------------
# deactivate_missing_skus
# ---------------------------------------------------------------------------


async def test_deactivate_missing_skus_only_touches_the_given_chain(
    repository: object, pg_pool: object
) -> None:
    la_anonima = await repository.chain_id("la_anonima")  # type: ignore[attr-defined]
    coto = await repository.chain_id("coto_digital")  # type: ignore[attr-defined]
    assert la_anonima is not None and coto is not None
    product_id = await _make_product(repository, "Compartido 1 kg")
    sku_la = await _make_sku(
        repository, product_id=product_id, chain_id=la_anonima, external_id="S1", name="S"
    )
    sku_coto = await _make_sku(
        repository, product_id=product_id, chain_id=coto, external_id="S2", name="S"
    )
    # simulating: SKU de coto fue visto "ahora", el de la_anonima quedó atrás
    async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
        await conn.execute(
            "UPDATE product_skus SET last_seen_at = $1 WHERE id = $2",
            datetime.now(UTC),
            sku_coto,
        )
        await conn.execute(
            "UPDATE product_skus SET last_seen_at = $1 WHERE id = $2",
            datetime.now(UTC) - timedelta(days=2),
            sku_la,
        )

    deactivated = await repository.deactivate_missing_skus(  # type: ignore[attr-defined]
        la_anonima, datetime.now(UTC) - timedelta(days=1)
    )
    assert deactivated == 1
    states = {
        row["id"]: row["active"]
        for row in await repository._provider.pool.fetch(  # type: ignore[attr-defined]
            "SELECT id, active FROM product_skus"
        )
    }
    assert states[sku_la] is False
    assert states[sku_coto] is True


async def test_deactivate_missing_skus_is_idempotent(
    repository: object, chain_id: int
) -> None:
    product_id = await _make_product(repository, "Idempotente 1 l")
    await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="I1", name="I"
    )
    first = await repository.deactivate_missing_skus(chain_id, datetime.now(UTC))  # type: ignore[attr-defined]
    second = await repository.deactivate_missing_skus(chain_id, datetime.now(UTC))  # type: ignore[attr-defined]
    assert first == 1
    assert second == 0


async def test_deactivate_missing_skus_returns_zero_when_nothing_missing(
    repository: object, chain_id: int
) -> None:
    assert await repository.deactivate_missing_skus(chain_id, datetime.now(UTC)) == 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# product_detail
# ---------------------------------------------------------------------------


async def test_product_detail_unknown_id_is_none(repository: object) -> None:
    assert await repository.product_detail(999_999) is None  # type: ignore[attr-defined]


async def test_product_detail_returns_one_representative_per_chain(
    repository: object,
) -> None:
    product_id = await _make_product(repository, "Detalle Multi 1 kg")
    la_anonima = await repository.chain_id("la_anonima")  # type: ignore[attr-defined]
    coto = await repository.chain_id("coto_digital")  # type: ignore[attr-defined]
    assert la_anonima is not None and coto is not None
    scenario = {la_anonima: [("DA1", "3000.00"), ("DA2", "2500.00")], coto: [("DC1", "2700.00")]}
    for chain, skus in scenario.items():
        for ext, price in skus:
            sku = await _make_sku(
                repository, product_id=product_id, chain_id=chain, external_id=ext, name="D"
            )
            await _make_point(
                repository,
                product_id=product_id,
                chain_id=chain,
                sku_id=sku,
                price=price,
                captured_at=BASE_DAY,
            )
    detail = await repository.product_detail(product_id)  # type: ignore[attr-defined]
    assert detail is not None
    by_chain = {p.chain: p.price for p in detail.prices}
    assert by_chain == {"la_anonima": "2500.00", "coto_digital": "2700.00"}


# ---------------------------------------------------------------------------
# price_history
# ---------------------------------------------------------------------------


async def test_price_history_filters_by_span(repository: object, chain_id: int) -> None:
    product_id = await _make_product(repository, "Historico 1 l")
    sku_id = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="H1", name="H"
    )
    now = datetime.now(UTC)
    for days_ago, price in ((1, "100.00"), (10, "200.00"), (60, "300.00")):
        await _make_point(
            repository,
            product_id=product_id,
            chain_id=chain_id,
            sku_id=sku_id,
            price=price,
            captured_at=now - timedelta(days=days_ago),
        )
    assert len(await repository.price_history(product_id, "7d")) == 1  # type: ignore[attr-defined]
    assert len(await repository.price_history(product_id, "30d")) == 2  # type: ignore[attr-defined]
    assert len(await repository.price_history(product_id, "90d")) == 3  # type: ignore[attr-defined]
    # span desconocido cae al default de 30d
    assert len(await repository.price_history(product_id, "no-existe")) == 2  # type: ignore[attr-defined]
    assert len(await repository.price_history(product_id, None)) == 2  # type: ignore[attr-defined]


async def test_price_history_filters_by_chain(repository: object) -> None:
    product_id = await _make_product(repository, "Hist Filtro 1 l")
    la_anonima = await repository.chain_id("la_anonima")  # type: ignore[attr-defined]
    coto = await repository.chain_id("coto_digital")  # type: ignore[attr-defined]
    assert la_anonima is not None and coto is not None
    for chain, ext in ((la_anonima, "HF1"), (coto, "HF2")):
        sku = await _make_sku(
            repository, product_id=product_id, chain_id=chain, external_id=ext, name="H"
        )
        await _make_point(
            repository,
            product_id=product_id,
            chain_id=chain,
            sku_id=sku,
            price="500.00",
            captured_at=datetime.now(UTC),
        )
    filtered = await repository.price_history(product_id, "30d", "coto_digital")  # type: ignore[attr-defined]
    assert {p.chain for p in filtered} == {"coto_digital"}
    unknown = await repository.price_history(product_id, "30d", "changomas")  # type: ignore[attr-defined]
    assert unknown == []


# ---------------------------------------------------------------------------
# price_series_for_forecast
# ---------------------------------------------------------------------------


async def test_price_series_groups_by_day_with_average(
    repository: object, chain_id: int
) -> None:
    """Varios puntos del mismo día colapsan a 1 con promedio."""
    product_id = await _make_product(repository, "Serie Diaria 1 l")
    sku_id = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="SE1", name="S"
    )
    await _make_point(
        repository,
        product_id=product_id,
        chain_id=chain_id,
        sku_id=sku_id,
        price="100.00",
        captured_at=BASE_DAY.replace(hour=9),
    )
    await _make_point(
        repository,
        product_id=product_id,
        chain_id=chain_id,
        sku_id=sku_id,
        price="200.00",
        captured_at=BASE_DAY.replace(hour=18),
    )
    await _make_point(
        repository,
        product_id=product_id,
        chain_id=chain_id,
        sku_id=sku_id,
        price="300.00",
        captured_at=BASE_DAY + timedelta(days=1),
    )
    series = await repository.price_series_for_forecast(product_id)  # type: ignore[attr-defined]
    assert len(series) == 2
    assert series[0][0] == BASE_DAY.date()
    assert series[0][1] == Decimal("150.00")
    assert series[1][1] == Decimal("300.00")


async def test_price_series_mixes_chains_when_no_filter(
    repository: object,
) -> None:
    product_id = await _make_product(repository, "Serie Multi 1 l")
    la_anonima = await repository.chain_id("la_anonima")  # type: ignore[attr-defined]
    coto = await repository.chain_id("coto_digital")  # type: ignore[attr-defined]
    assert la_anonima is not None and coto is not None
    for chain, ext, price in ((la_anonima, "SM1", "100.00"), (coto, "SM2", "300.00")):
        sku = await _make_sku(
            repository, product_id=product_id, chain_id=chain, external_id=ext, name="S"
        )
        await _make_point(
            repository,
            product_id=product_id,
            chain_id=chain,
            sku_id=sku,
            price=price,
            captured_at=BASE_DAY,
        )
    combined = await repository.price_series_for_forecast(product_id)  # type: ignore[attr-defined]
    assert combined[0][1] == Decimal("200.00")
    only_coto = await repository.price_series_for_forecast(  # type: ignore[attr-defined]
        product_id, "coto_digital"
    )
    assert only_coto[0][1] == Decimal("300.00")


# ---------------------------------------------------------------------------
# ml_candidates
# ---------------------------------------------------------------------------


async def test_ml_candidates_counts_distinct_days(repository: object, chain_id: int) -> None:
    """Criterio de la ronda 2026-09-25: cuentan días, no filas."""
    product_id = await _make_product(repository, "ML Candidato 1 l")
    sku_id = await _make_sku(
        repository, product_id=product_id, chain_id=chain_id, external_id="ML1", name="M"
    )
    start = datetime.now(UTC) - timedelta(days=20)
    for offset in range(15):
        day = start + timedelta(days=offset)
        # dos capturas por día: siguen siendo 15 días
        await _make_point(
            repository,
            product_id=product_id,
            chain_id=chain_id,
            sku_id=sku_id,
            price=f"{100 + offset}.00",
            captured_at=day.replace(hour=9),
        )
        await _make_point(
            repository,
            product_id=product_id,
            chain_id=chain_id,
            sku_id=sku_id,
            price=f"{200 + offset}.00",
            captured_at=day.replace(hour=20),
        )
    candidates = await repository.ml_candidates(14)  # type: ignore[attr-defined]
    assert (product_id, chain_id, "la_anonima", 15) in candidates
    assert await repository.ml_candidates(20) == []  # type: ignore[attr-defined]


async def test_ml_candidates_excludes_inactive_chains(
    repository: object, pg_pool: object
) -> None:
    product_id = await _make_product(repository, "ML Inactiva 1 l")
    coto = await repository.chain_id("coto_digital")  # type: ignore[attr-defined]
    assert coto is not None
    sku_id = await _make_sku(
        repository, product_id=product_id, chain_id=coto, external_id="MI1", name="M"
    )
    start = datetime.now(UTC) - timedelta(days=20)
    for offset in range(15):
        await _make_point(
            repository,
            product_id=product_id,
            chain_id=coto,
            sku_id=sku_id,
            price=f"{100 + offset}.00",
            captured_at=start + timedelta(days=offset),
        )
    async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
        await conn.execute("UPDATE chains SET active = false WHERE slug = 'coto_digital'")
    try:
        candidates = await repository.ml_candidates(10)  # type: ignore[attr-defined]
        assert all(slug != "coto_digital" for _, _, slug, _ in candidates)
    finally:
        async with pg_pool.acquire() as conn:  # type: ignore[attr-defined]
            await conn.execute("UPDATE chains SET active = true WHERE slug = 'coto_digital'")


# ---------------------------------------------------------------------------
# save_forecast / save_anomalies / save_ml_metrics
# ---------------------------------------------------------------------------


async def test_save_forecast_persists_horizon_days(
    repository: object, chain_id: int
) -> None:
    """Regresión de la ronda 2026-09-25: antes se guardaba siempre 1."""
    product_id = await _make_product(repository, "Forecast 1 l")
    written = await repository.save_forecast(  # type: ignore[attr-defined]
        product_id=product_id,
        chain_id=chain_id,
        model="holtwinters",
        points=[
            (date(2026, 10, 1), Decimal("100.00"), Decimal("95.00"), Decimal("105.00")),
            (date(2026, 10, 2), Decimal("101.00"), Decimal("96.00"), Decimal("106.00")),
        ],
        horizon_days=7,
    )
    assert written == 2
    rows = await repository._provider.pool.fetch(  # type: ignore[attr-defined]
        "SELECT forecast_date, horizon_days FROM forecasts ORDER BY forecast_date"
    )
    assert [r["forecast_date"] for r in rows] == [date(2026, 10, 1), date(2026, 10, 2)]
    assert {r["horizon_days"] for r in rows} == {7}


async def test_save_forecast_is_idempotent(repository: object, chain_id: int) -> None:
    product_id = await _make_product(repository, "Forecast Idem 1 l")
    kwargs = {
        "product_id": product_id,
        "chain_id": chain_id,
        "model": "holtwinters",
        "points": [(date(2026, 10, 1), Decimal("100.00"), Decimal("95.00"), Decimal("105.00"))],
        "horizon_days": 7,
    }
    await repository.save_forecast(**kwargs)  # type: ignore[attr-defined]
    await repository.save_forecast(  # type: ignore[attr-defined]
        **{**kwargs, "points": [(date(2026, 10, 1), Decimal("111.00"), None, None)]}
    )
    rows = await repository._provider.pool.fetch(  # type: ignore[attr-defined]
        "SELECT predicted_price, conf_low FROM forecasts"
    )
    assert len(rows) == 1
    assert rows[0]["predicted_price"] == Decimal("111.00")
    assert rows[0]["conf_low"] is None


async def test_save_forecast_rejects_unknown_model(
    repository: object, chain_id: int
) -> None:
    """El CHECK de 008_ml.sql es una barrera real: no dejar pasar basura."""
    product_id = await _make_product(repository, "Forecast Modelo 1 l")
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await repository.save_forecast(  # type: ignore[attr-defined]
            product_id=product_id,
            chain_id=chain_id,
            model="modelo_inventado",
            points=[(date(2026, 10, 1), Decimal("1.00"), None, None)],
        )


async def test_save_anomalies_is_idempotent(repository: object, chain_id: int) -> None:
    product_id = await _make_product(repository, "Anomalias 1 l")
    rows_in = [(BASE_DAY, Decimal("5000.00"), 0.91)]
    assert await repository.save_anomalies(  # type: ignore[attr-defined]
        product_id=product_id, chain_id=chain_id, rows=rows_in
    ) == 1
    assert await repository.save_anomalies(  # type: ignore[attr-defined]
        product_id=product_id, chain_id=chain_id, rows=rows_in
    ) == 1
    stored = await repository._provider.pool.fetch(  # type: ignore[attr-defined]
        "SELECT product_id, chain_id, price, score, is_anomaly FROM anomalies"
    )
    assert len(stored) == 1
    assert stored[0]["price"] == Decimal("5000.00")
    assert stored[0]["is_anomaly"] is True


async def test_save_anomalies_with_no_rows_is_noop(
    repository: object, chain_id: int
) -> None:
    product_id = await _make_product(repository, "Anomalias Vacias 1 l")
    assert await repository.save_anomalies(  # type: ignore[attr-defined]
        product_id=product_id, chain_id=chain_id, rows=[]
    ) == 0
    assert await repository._provider.pool.fetchval(  # type: ignore[attr-defined]
        "SELECT count(*) FROM anomalies"
    ) == 0


async def test_save_ml_metrics_inserts_then_updates(
    repository: object, chain_id: int
) -> None:
    """Las filas de Python y .NET conviven (comparativo del ADR-0006)."""
    product_id = await _make_product(repository, "Metricas 1 l")
    for language, model, mape in (("python", "holtwinters", 0.72), ("dotnet", "mlnet", 1.28)):
        await repository.save_ml_metrics(  # type: ignore[attr-defined]
            product_id=product_id,
            chain_id=chain_id,
            model=model,
            language=language,
            mape=mape,
            rmse=90.0,
            mae=60.0,
            n_points=60,
        )
    rows = await repository._provider.pool.fetch(  # type: ignore[attr-defined]
        "SELECT language, model, mape FROM ml_models ORDER BY language"
    )
    assert [(r["language"], r["model"], r["mape"]) for r in rows] == [
        ("dotnet", "mlnet", Decimal("1.2800")),
        ("python", "holtwinters", Decimal("0.7200")),
    ]

    await repository.save_ml_metrics(  # type: ignore[attr-defined]
        product_id=product_id,
        chain_id=chain_id,
        model="holtwinters",
        language="python",
        mape=0.5,
        rmse=80.0,
        mae=50.0,
        n_points=120,
    )
    assert await repository._provider.pool.fetchval(  # type: ignore[attr-defined]
        "SELECT count(*) FROM ml_models"
    ) == 2
    updated = await repository._provider.pool.fetchrow(  # type: ignore[attr-defined]
        "SELECT mape, n_points FROM ml_models WHERE language = 'python'"
    )
    assert updated["mape"] == Decimal("0.5000")
    assert updated["n_points"] == 120


async def test_save_ml_metrics_rejects_unknown_language(
    repository: object, chain_id: int
) -> None:
    product_id = await _make_product(repository, "Metricas Lang 1 l")
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await repository.save_ml_metrics(  # type: ignore[attr-defined]
            product_id=product_id,
            chain_id=chain_id,
            model="holtwinters",
            language="rust",
            mape=1.0,
            rmse=1.0,
            mae=1.0,
            n_points=1,
        )