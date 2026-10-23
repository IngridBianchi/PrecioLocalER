"""Tests de la capa de red de Coto Digital (BFF Constructor.io).

`parse_search` ya estaba cubierto con la fixture real. Lo que faltaba es la parte
que decide el volumen de requests y el fail-closed de la API key:

- `_search_url` construye `page` + `offset`: si la paginación se desalinea, se
  repiten productos o se salta la mitad del catálogo en silencio.
- Sin `COTO_DIGITAL_API_KEY` el adaptador debe fallar, no devolver `[]` (un `[]`
  silencioso en el runner significa soft-delete de todos los SKU: pérdida de
  datos). Ver ADR-0007 y el test homónimo.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from app.config import Settings
from app.errors import RobotsDeniedError, UpstreamError
from app.scrapers.coto_digital import CotoDigitalScraper

API_BASE = "https://api.coto.com.ar/api/v1/ms-digital-sitio-bff-web/api/v1"
ROBOTS = "User-agent: *\nAllow: /\nDisallow: /admin/\n"


@pytest.fixture
def coto_settings() -> Settings:
    return Settings(
        app_environment="test",
        database_url="",
        redis_url="",
        scraper_min_request_interval=0,
        scraper_retries=1,
        coto_digital_api_key="test-key",
        coto_digital_search_terms="aceite girasol",
        coto_digital_results_per_page=2,
    )


def _mock_robots() -> None:
    """`api.coto.com.ar` no tiene robots propio: el enforcer usa el alias del
    sitio principal (`www.cotodigital.com.ar`), ADR-0007."""
    respx.get("https://www.cotodigital.com.ar/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS)
    )


def _payload(results: list[dict[str, Any]], total: int) -> dict[str, Any]:
    return {"response": {"results": results, "total_num_results": total}}


def _result(sku: str, name: str, price: Any = 5510) -> dict[str, Any]:
    return {
        "data": {
            "sku_plu": sku,
            "sku_description": name,
            "url": f"_/R-{sku}-{sku}-200",
            "price": [
                {"store": "1", "listPrice": 9999},
                {"store": "200", "listPrice": price},
            ],
        }
    }


def _page_url(term: str, page: int, per_page: int = 2) -> str:
    return (
        f"{API_BASE}/products/search/{term}"
        f"?key=test-key&num_results_per_page={per_page}"
        "&pre_filter_expression=%7B%22name%22%3A%22store_availability%22"
        "%2C%22value%22%3A%22200%22%7D&us=200"
        f"&page={page}&offset={(page - 1) * per_page}"
    )


@respx.mock
@pytest.mark.asyncio
async def test_fetch_term_paginates_page_and_offset(coto_settings: Settings) -> None:
    """Página 1 (offset 0) y página 2 (offset = per_page) deben-hitear."""
    _mock_robots()
    first = respx.get(_page_url("aceite%20girasol", 1)).mock(
        return_value=httpx.Response(200, json=_payload([_result("1", "A"), _result("2", "B")], 3))
    )
    second = respx.get(_page_url("aceite%20girasol", 2)).mock(
        return_value=httpx.Response(200, json=_payload([_result("3", "C")], 3))
    )

    async with httpx.AsyncClient() as client:
        scraper = CotoDigitalScraper(client=client, settings=coto_settings)
        products = await scraper.fetch_term("aceite girasol")

    assert first.call_count == 1
    assert second.call_count == 1
    assert [p.external_id for p in products] == ["1", "2", "3"]


@respx.mock
@pytest.mark.asyncio
async def test_fetch_term_stops_on_empty_page(coto_settings: Settings) -> None:
    """Página vacía = fin del catálogo: no se pide la siguiente."""
    _mock_robots()
    first = respx.get(_page_url("aceite%20girasol", 1)).mock(
        return_value=httpx.Response(200, json=_payload([_result("1", "A")], 99))
    )
    second = respx.get(_page_url("aceite%20girasol", 2)).mock(
        return_value=httpx.Response(200, json=_payload([], 99))
    )

    async with httpx.AsyncClient() as client:
        scraper = CotoDigitalScraper(client=client, settings=coto_settings)
        products = await scraper.fetch_term("aceite girasol")

    assert len(products) == 1
    assert first.call_count == 1
    assert second.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_scrape_catalog_uses_configured_terms(coto_settings: Settings) -> None:
    _mock_robots()
    route = respx.get(_page_url("aceite%20girasol", 1)).mock(
        return_value=httpx.Response(200, json=_payload([_result("1", "A")], 1))
    )

    async with httpx.AsyncClient() as client:
        scraper = CotoDigitalScraper(client=client, settings=coto_settings)
        products = await scraper.scrape_catalog()

    assert route.call_count == 1
    assert len(products) == 1


@pytest.mark.asyncio
async def test_missing_api_key_fails_closed(coto_settings: Settings) -> None:
    """Sin API key: `UpstreamError` antes de cualquier request HTTP.

    Devolver `[]` sería peor que fallar: el runner interpretaría "sin productos"
    y haría soft-delete de todos los SKU de Coto en la base.
    """
    coto_settings.coto_digital_api_key = ""

    async with httpx.AsyncClient() as client:
        scraper = CotoDigitalScraper(client=client, settings=coto_settings)
        with pytest.raises(UpstreamError, match="COTO_DIGITAL_API_KEY"):
            await scraper.scrape_catalog()


@respx.mock
@pytest.mark.asyncio
async def test_api_origin_is_governed_by_site_robots(coto_settings: Settings) -> None:
    """El alias hace que el robots del sitio principal gobierne la API.

    Si el sitio principal empieza a bloquear `/api`, el scraper debe parar.
    """
    respx.get("https://www.cotodigital.com.ar/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /api/\n")
    )

    async with httpx.AsyncClient() as client:
        scraper = CotoDigitalScraper(client=client, settings=coto_settings)
        with pytest.raises(RobotsDeniedError):
            await scraper.scrape_catalog()
