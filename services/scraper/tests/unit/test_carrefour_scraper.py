"""Tests del scraper de Carrefour (VTEX catalog_system).

`parse_catalog` ya estaba cubierto contra la fixture real; lo que faltaba era la
capa de red: paginación, parseo del header `resources` y el comportamiento del
robots enforcer (que es lo que decide si el adaptador puede pegarle al sitio).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from app.config import Settings
from app.scrapers.carrefour import CarrefourScraper, _parse_total

BASE = "https://www.carrefour.com.ar/api/catalog_system/pub/products/search"


def test_parse_total_reads_resources_header() -> None:
    assert _parse_total({"resources": "0-24/75"}) == 75
    assert _parse_total({"resources": "0-9/37681"}) == 37681


def test_parse_total_absent_or_malformed() -> None:
    assert _parse_total({}) is None
    assert _parse_total(None) is None
    assert _parse_total({"resources": "sin-formato"}) is None


class _RespxItems:
    """Genera respuestas VTEX con el shape de `parse_catalog`."""

    @staticmethod
    def payload(count: int, start: int) -> list[dict[str, Any]]:
        return [
            {
                "productId": str(start + i),
                "productName": f"Producto {start + i}",
                "link": f"https://www.carrefour.com.ar/p{start + i}/p",
                "categories": ["/Almacen/Aceites/"],
                "items": [
                    {
                        "sellers": [
                            {
                                "commertialOffer": {
                                    "Price": 100.0,
                                    "ListPrice": 100.0,
                                    "IsAvailable": True,
                                    "AvailableQuantity": 2,
                                }
                            }
                        ]
                    }
                ],
            }
            for i in range(count)
        ]


def _mock_robots() -> None:
    respx.get("https://www.carrefour.com.ar/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )

@pytest.fixture
def carrefour_settings() -> Settings:
    """Settings con categorías propias: el fixture global trae los defaults de
    producción y sus URLs no están mockeadas en estos tests."""
    return Settings(
        app_environment="test",
        database_url="",
        redis_url="",
        scraper_min_request_interval=0,
        scraper_retries=1,
        carrefour_categories="almacen,harinas",
    )


@respx.mock
@pytest.mark.asyncio
async def test_fetch_category_paginates_until_total(settings) -> None:
    _mock_robots()
    respx.get(f"{BASE}/almacen?_from=0&_to=24").mock(
        return_value=httpx.Response(
            206,
            json=_RespxItems.payload(25, 0),
            headers={"resources": "0-24/30"},
        )
    )
    respx.get(f"{BASE}/almacen?_from=25&_to=49").mock(
        return_value=httpx.Response(
            206, json=_RespxItems.payload(5, 25), headers={"resources": "25-29/30"}
        )
    )

    async with httpx.AsyncClient() as client:
        scraper = CarrefourScraper(client=client, settings=settings)
        products = await scraper.fetch_category("almacen")

    assert len(products) == 30
    assert products[0].price == Decimal("100")


@respx.mock
@pytest.mark.asyncio
async def test_fetch_category_stops_without_resources_header(settings) -> None:
    """Sin total no se puede paginar: una sola página, sin reintentos."""
    _mock_robots()
    route = respx.get(f"{BASE}/almacen?_from=0&_to=24").mock(
        return_value=httpx.Response(200, json=_RespxItems.payload(3, 0))
    )

    async with httpx.AsyncClient() as client:
        scraper = CarrefourScraper(client=client, settings=settings)
        products = await scraper.fetch_category("almacen")

    assert len(products) == 3
    assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_fetch_category_handles_empty_catalog(settings) -> None:
    _mock_robots()
    respx.get(f"{BASE}/vacio?_from=0&_to=24").mock(
        return_value=httpx.Response(200, json=[], headers={"resources": "0-24/0"})
    )

    async with httpx.AsyncClient() as client:
        scraper = CarrefourScraper(client=client, settings=settings)
        assert await scraper.fetch_category("vacio") == []


@respx.mock
@pytest.mark.asyncio
async def test_scrape_catalog_uses_configured_categories(carrefour_settings) -> None:
    _mock_robots()
    first = respx.get(f"{BASE}/almacen?_from=0&_to=24").mock(
        return_value=httpx.Response(
            206, json=_RespxItems.payload(2, 0), headers={"resources": "0-24/2"}
        )
    )
    second = respx.get(f"{BASE}/harinas?_from=0&_to=24").mock(
        return_value=httpx.Response(
            206, json=_RespxItems.payload(2, 50), headers={"resources": "0-24/2"}
        )
    )

    async with httpx.AsyncClient() as client:
        scraper = CarrefourScraper(client=client, settings=carrefour_settings)
        products = await scraper.scrape_catalog()

    assert first.called and second.called
    assert len(products) == 4


@respx.mock
@pytest.mark.asyncio
async def test_scrape_catalog_propagates_upstream_error(carrefour_settings) -> None:
    """Un 500 tiene que llegar como `UpstreamError`, no como lista vacía.

    Devolver `[]` en silencio haría que el runner desactivara todos los SKU
    (soft-delete) por una caída del sitio: pérdida de datos.
    """
    from app.errors import UpstreamError

    _mock_robots()
    respx.get(f"{BASE}/almacen?_from=0&_to=24").mock(return_value=httpx.Response(500))

    async with httpx.AsyncClient() as client:
        scraper = CarrefourScraper(client=client, settings=carrefour_settings)
        with pytest.raises(UpstreamError):
            await scraper.scrape_catalog()
