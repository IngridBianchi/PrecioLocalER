"""Integration tests de `ScrapeRunner` contra Postgres real.

`app/pipelines/runner.py` estaba al 21% de cobertura y es la orquestación
completa de un scrape: fetch -> normalizar -> upsert producto -> upsert SKU ->
insert price point -> barrido de SKU dados de baja. Un error acá no lo agarra
ningún test de parser ni de API, así que se prueba entero y contra la base.

El scraper es un fake que devuelve `RawProduct` listos: lo que se verifica acá
es la persistencia y las reglas, no el HTML ( eso lo cubren los tests de
parser con fixtures reales).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.models import RawProduct
from app.pipelines.runner import ScrapeRunner

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class FakeScraper:
    """Adaptador que devuelve un catálogo fijo y registra si lo llamaron."""

    def __init__(self, products: list[RawProduct] | None = None, *, error: Exception | None = None):
        self.products = products or []
        self.error = error
        self.calls = 0

    async def scrape_catalog(self) -> list[RawProduct]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.products


def _raw(
    *,
    external_id: str,
    name: str,
    price: str,
    captured_at: datetime | None = None,
    promo_type: str | None = None,
) -> RawProduct:
    return RawProduct(
        external_id=external_id,
        name=name,
        url=f"https://example.test/p/{external_id}",
        price=Decimal(price),
        promo_type=promo_type,
        captured_at=captured_at or datetime.now(UTC),
    )


class ExplodingRepo:
    """Repositorio que falla al upsertar el producto con `bad_name`.

    El runner promete que un producto problemático no tira abajo el lote, así
    que para ejercitarlo hace falta que el fallo ocurra *dentro* del loop. Ya no
    se puede provocar con un `RawProduct` inválido: la validación vive en el
    modelo (ver tests/unit/test_models.py), que revienta antes de llegar al
    runner. Lo realisticamente restante es un error de base —una restricción,
    un numeric desborde, una conexión que se cae— y eso es lo que se simula.
    """

    def __init__(self, inner: object, bad_names: set[str], error: Exception | None = None) -> None:
        self._inner = inner
        self._bad_names = bad_names
        self._error = error or RuntimeError("duplicate key value violates unique constraint")

    def __getattr__(self, name: str):  # noqa: ANN401
        return getattr(self._inner, name)

    async def upsert_product(self, product):  # noqa: ANN001, ANN201
        if product.name in self._bad_names:
            raise self._error
        return await self._inner.upsert_product(product)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------------


async def test_run_persists_product_sku_and_point(repository: object, chain_id: int) -> None:
    products = [
        _raw(external_id="1", name="Aceite de Girasol 900 cc", price="5350.00"),
        _raw(external_id="2", name="Harina de Trigo 000 1 kg", price="2400.00"),
    ]
    runner = ScrapeRunner(FakeScraper(products), repository, "la_anonima")  # type: ignore[arg-type]
    summary = await runner.run()

    assert summary.chain == "la_anonima"
    assert summary.products_found == 2
    assert summary.products_upserted == 2
    assert summary.price_points_inserted == 2
    assert summary.price_points_skipped == 0
    assert summary.errors == 0

    detail = await repository.product_detail(  # type: ignore[attr-defined]
        int((await repository.search_products("aceite de girasol", None)).products[0].id)  # type: ignore[attr-defined]
    )
    assert detail is not None
    assert detail.prices[0].price == "5350.00"


async def test_run_is_idempotent_on_second_pass(
    repository: object, chain_id: int
) -> None:
    """Re-scrapear lo mismo no duplica: `ON CONFLICT` por hash."""
    products = [_raw(external_id="1", name="Leche 1 l", price="1500.00")]
    first = await ScrapeRunner(  # type: ignore[arg-type]
        FakeScraper(products), repository, "la_anonima"
    ).run()
    second = await ScrapeRunner(  # type: ignore[arg-type]
        FakeScraper(products), repository, "la_anonima"
    ).run()
    assert first.price_points_inserted == 1
    assert second.price_points_inserted == 0
    assert second.price_points_skipped == 1
    assert second.errors == 0


async def test_run_keeps_two_skus_of_the_same_product_apart(
    repository: object, chain_id: int
) -> None:
    """Regresión del incidente 2026-09-24 de punta a punta."""
    name = "Harina De Trigo Chacabuco Paquete 750 Gr"
    products = [
        _raw(external_id="531487", name=name, price="2129.00"),
        _raw(external_id="531488", name=name, price="2899.00"),
    ]
    summary = await ScrapeRunner(  # type: ignore[arg-type]
        FakeScraper(products), repository, "la_anonima"
    ).run()
    assert summary.products_upserted == 2
    assert summary.price_points_inserted == 2

    page = await repository.search_products("chacabuco", None)  # type: ignore[attr-defined]
    assert len(page.products) == 1, "los dos SKU colapsan en un solo producto canónico"
    assert page.products[0].price == "2129.00", "el representativo es el menor precio de la cadena"


# ---------------------------------------------------------------------------
# Cadena desconocida
# ---------------------------------------------------------------------------


async def test_run_raises_when_chain_not_seeded(repository: object) -> None:
    runner = ScrapeRunner(FakeScraper([]), repository, "cadena_inexistente")  # type: ignore[arg-type]
    with pytest.raises(LookupError, match="no seedeada"):
        await runner.run()


# ---------------------------------------------------------------------------
# Errores por producto: no deben tirar el lote
# ---------------------------------------------------------------------------


async def test_one_bad_product_does_not_abort_the_batch(
    repository: object, chain_id: int
) -> None:
    """Si un producto explota, el resto se persiste y el error queda contabilizado."""
    products = [
        _raw(external_id="1", name="Bueno 1 l", price="100.00"),
        _raw(external_id="2", name="Explota 1 l", price="200.00"),
        _raw(external_id="3", name="Bueno 2 l", price="300.00"),
    ]
    summary = await ScrapeRunner(  # type: ignore[arg-type]
        FakeScraper(products), ExplodingRepo(repository, {"Explota 1 l"}), "la_anonima"
    ).run()
    assert summary.errors == 1
    assert summary.products_upserted == 2
    assert summary.price_points_inserted == 2
    # El lote se completó: el producto posterior al que falló sí entró.
    assert (await repository.search_products("bueno 2 l", None)).products  # type: ignore[attr-defined]
    assert (await repository.search_products("explota", None)).products == []  # type: ignore[attr-defined]


async def test_summary_reports_every_failed_product(
    repository: object, chain_id: int
) -> None:
    """El conteo de errores es por producto, no un "hubo algún error"."""
    products = [
        _raw(external_id="1", name="Mala 1 l", price="100.00"),
        _raw(external_id="2", name="Mala 2 l", price="200.00"),
        _raw(external_id="3", name="Buena 1 l", price="300.00"),
    ]
    summary = await ScrapeRunner(  # type: ignore[arg-type]
        FakeScraper(products), ExplodingRepo(repository, {"Mala 1 l", "Mala 2 l"}), "la_anonima"
    ).run()
    assert summary.errors == 2
    assert summary.price_points_inserted == 1


# ---------------------------------------------------------------------------
# Soft-delete de SKU (deuda T8)
# ---------------------------------------------------------------------------


async def test_run_deactivates_skus_that_disappeared_from_the_chain(
    repository: object, chain_id: int
) -> None:
    first_pass = [
        _raw(external_id="A", name="Permanente 1 l", price="100.00"),
        _raw(external_id="B", name="Temporal 1 l", price="200.00"),
    ]
    await ScrapeRunner(FakeScraper(first_pass), repository, "la_anonima").run()  # type: ignore[arg-type]
    assert len((await repository.search_products("temporal", None)).products) == 1  # type: ignore[attr-defined]

    # Segunda pasada: la cadena sacó "Temporal" de la góndola.
    second_pass = [_raw(external_id="A", name="Permanente 1 l", price="110.00")]
    summary = await ScrapeRunner(  # type: ignore[arg-type]
        FakeScraper(second_pass), repository, "la_anonima"
    ).run()

    assert summary.skus_deactivated == 1
    assert (await repository.search_products("temporal", None)).products == []  # type: ignore[attr-defined]
    assert len((await repository.search_products("permanente", None)).products) == 1  # type: ignore[attr-defined]


async def test_run_does_not_deactivate_when_the_scrape_failed(
    repository: object, chain_id: int
) -> None:
    """Un scrape con errores no es evidencia de que algo dejó devenderse."""
    await ScrapeRunner(  # type: ignore[arg-type]
        FakeScraper([_raw(external_id="A", name="Frágil 1 l", price="100.00")]),
        repository,
        "la_anonima",
    ).run()

    broken = [
        _raw(external_id="A", name="Frágil 1 l", price="110.00"),
        _raw(external_id="B", name="Frágil 2 l", price="120.00"),  # fuerza summary.errors > 0
    ]
    summary = await ScrapeRunner(  # type: ignore[arg-type]
        FakeScraper(broken), ExplodingRepo(repository, "Frágil 2 l"), "la_anonima"
    ).run()
    assert summary.errors > 0
    assert summary.skus_deactivated == 0
    assert len((await repository.search_products("frágil", None)).products) == 1  # type: ignore[attr-defined]


async def test_run_counts_the_deactivation_failure_as_an_error(
    repository: object, chain_id: int
) -> None:
    """Si el barrido falla, no se lo come: sube `errors`."""
    products = [_raw(external_id="A", name="Barrido 1 l", price="100.00")]

    class FailingDeactivate:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        def __getattr__(self, name: str):  # noqa: ANN401
            return getattr(self._inner, name)

        async def deactivate_missing_skus(self, chain_id: int, seen_after: datetime) -> int:
            raise RuntimeError("boom")

    summary = await ScrapeRunner(  # type: ignore[arg-type]
        FakeScraper(products), FailingDeactivate(repository), "la_anonima"
    ).run()
    assert summary.skus_deactivated == 0
    assert summary.errors == 1


# ---------------------------------------------------------------------------
# Normalización y captions
# ---------------------------------------------------------------------------


async def test_run_extracts_size_from_the_name(
    repository: object, chain_id: int
) -> None:
    await ScrapeRunner(  # type: ignore[arg-type]
        FakeScraper([_raw(external_id="S", name="Aceite de Girasol 900 cc", price="5350.00")]),
        repository,
        "la_anonima",
    ).run()
    row = (await repository.search_products("aceite", None)).products[0]  # type: ignore[attr-defined]
    assert row.size_unit == "cc"
    assert row.size_quantity == Decimal("900")


async def test_run_preserves_promo_type(repository: object, chain_id: int) -> None:
    await ScrapeRunner(  # type: ignore[arg-type]
        FakeScraper(
            [_raw(external_id="P", name="Promo 1 l", price="800.00", promo_type="25%_off")]
        ),
        repository,
        "la_anonima",
    ).run()
    row = (await repository.search_products("promo", None)).products[0]  # type: ignore[attr-defined]
    assert row.promo_type == "25%_off"


async def test_run_uses_scraper_capture_time_not_insert_time(
    repository: object, chain_id: int
) -> None:
    """`captured_at` viene del scraper: un catálogo cacheado no debe mentir."""
    captured = datetime.now(UTC) - timedelta(days=3)
    await ScrapeRunner(  # type: ignore[arg-type]
        FakeScraper(
            [_raw(external_id="T", name="Antiguo 1 l", price="100.00", captured_at=captured)]
        ),
        repository,
        "la_anonima",
    ).run()
    history = await repository.price_history(  # type: ignore[attr-defined]
        int((await repository.search_products("antiguo", None)).products[0].id), "7d"  # type: ignore[attr-defined]
    )
    assert len(history) == 1
    assert history[0].captured_at == captured


async def test_run_calls_the_scraper_exactly_once(
    repository: object, chain_id: int
) -> None:
    scraper = FakeScraper([_raw(external_id="Q", name="Unico 1 l", price="100.00")])
    await ScrapeRunner(scraper, repository, "la_anonima").run()  # type: ignore[arg-type]
    assert scraper.calls == 1