"""Tests del scraper de ChangoMás / MAS Online (fixture JSON real del VTEX).

La fixture es una página real de `products/search/arroz` (50 items) capturada el
2026-10-01. Los tests de parseo son puros; los de red van abajo con `respx`.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from app.scrapers.changomas import ChangomasScraper, parse_catalog, parse_total


def test_parses_all_available_products(changomas_arroz) -> None:
    products = parse_catalog(changomas_arroz)
    assert len(products) == len(changomas_arroz)


def test_name_external_id_price_and_url(changomas_arroz) -> None:
    by_id = {p.external_id: p for p in parse_catalog(changomas_arroz)}
    product = by_id["159851"]
    assert product.name == "Arroz Largo Fino Dos Hermanos 1 Kg"
    assert product.price == Decimal("1614.15")
    assert product.url == (
        "https://www.masonline.com.ar/arroz-largo-fino-dos-hermanos-1-kg-2/p"
    )


def test_promo_detected_from_list_price(changomas_arroz) -> None:
    by_id = {p.external_id: p for p in parse_catalog(changomas_arroz)}
    # 1899 -> 1614.15 es un 15% de descuento
    assert by_id["159851"].promo_type == "15%_off"


def test_no_promo_keeps_regular_price(changomas_arroz) -> None:
    by_id = {p.external_id: p for p in parse_catalog(changomas_arroz)}
    assert by_id["16211"].price == Decimal("1489")
    assert by_id["16211"].promo_type is None


def test_category_path_from_categories(changomas_arroz) -> None:
    by_id = {p.external_id: p for p in parse_catalog(changomas_arroz)}
    assert by_id["159851"].category_path == (
        "Arroz, Legumbres Y Pastas > Arroz > Arroz Largo Fino"
    )


def test_no_stock_product_skipped() -> None:
    item = {
        "productId": "1",
        "productName": "Sin stock",
        "link": "https://www.masonline.com.ar/x/p",
        "categories": ["/A/B/"],
        "items": [
            {
                "sellers": [
                    {
                        "commertialOffer": {
                            "Price": 100.0,
                            "ListPrice": 100.0,
                            "IsAvailable": True,
                            "AvailableQuantity": 0,
                        }
                    }
                ]
            }
        ],
    }
    assert parse_catalog([item]) == []


def test_zero_price_product_skipped() -> None:
    item = {
        "productId": "1",
        "productName": "Sin precio",
        "link": "https://www.masonline.com.ar/x/p",
        "categories": [],
        "items": [
            {
                "sellers": [
                    {
                        "commertialOffer": {
                            "Price": 0.0,
                            "ListPrice": 0.0,
                            "IsAvailable": True,
                            "AvailableQuantity": 5,
                        }
                    }
                ]
            }
        ],
    }
    assert parse_catalog([item]) == []


def test_missing_id_or_name_skipped() -> None:
    assert parse_catalog([{"productName": "x", "items": []}]) == []
    assert parse_catalog([{"productId": "1", "items": []}]) == []


def test_empty_payload_returns_empty() -> None:
    assert parse_catalog([]) == []


def test_parse_total_reads_resources_header() -> None:
    assert parse_total({"resources": "0-49/490"}) == 490
    assert parse_total({"resources": "0-9/37681"}) == 37681


def test_parse_total_absent_header() -> None:
    assert parse_total({}) is None
    assert parse_total({"resources": "sin-formato"}) is None
    assert parse_total(None) is None


def test_non_numeric_price_skipped() -> None:
    """Un `Price` no numérico no puede caer en la base: `Decimal` lanza."""
    item = _item("1")
    item["items"][0]["sellers"][0]["commertialOffer"]["Price"] = "a consultar"
    item["items"][0]["sellers"][0]["commertialOffer"]["ListPrice"] = None
    assert parse_catalog([item]) == []


def test_promo_without_list_price_is_not_a_promo() -> None:
    """`ListPrice` ausente: es precio regular, no un descuento del 100%."""
    item = _item("1")
    del item["items"][0]["sellers"][0]["commertialOffer"]["ListPrice"]
    product = parse_catalog([item])[0]
    assert product.promo_type is None
    assert product.price == Decimal("100")


def test_no_category_path_when_categories_empty() -> None:
    item = _item("1")
    item["categories"] = []
    assert parse_catalog([item])[0].category_path is None


def _item(product_id: str) -> dict[str, Any]:
    return {
        "productId": product_id,
        "productName": f"Producto {product_id}",
        "link": f"https://www.masonline.com.ar/p{product_id}/p",
        "categories": ["/A/B/"],
        "items": [
            {
                "sellers": [
                    {
                        "commertialOffer": {
                            "Price": 100.0,
                            "ListPrice": 100.0,
                            "IsAvailable": True,
                            "AvailableQuantity": 3,
                        }
                    }
                ]
            }
        ],
    }


# robots.txt real de masonline.com.ar, recortado a las reglas que tocan el
# adaptador. `/api/catalog_system` NO está bloqueado (por eso este scraper
# puede existir), mientras que `/buscapagina/` sí.
ROBOTS = """User-agent: *
Disallow: /img/*
Disallow: /account/*
Disallow: /login/*
Disallow: /checkout/*
Disallow: /busca/*
Disallow: /quick-view/*
Disallow: /espiar/*
Disallow: /buscapagina/*
Disallow: /metodos-de-pago/*
"""


def _mock_robots() -> None:
    respx.get("https://www.masonline.com.ar/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS)
    )


@respx.mock
@pytest.mark.asyncio
async def test_fetch_term_paginates_until_total(settings) -> None:
    _mock_robots()
    base = "https://www.masonline.com.ar/api/catalog_system/pub/products/search/arroz"

    def page(start: int, end: int) -> httpx.Response:
        return httpx.Response(
            206,
            json=[_item(str(start + i)) for i in range(end - start + 1)],
            headers={"resources": f"{start}-{end}/120"},
        )

    respx.get(f"{base}?_from=0&_to=49").mock(side_effect=lambda r: page(0, 49))
    respx.get(f"{base}?_from=50&_to=99").mock(side_effect=lambda r: page(50, 99))
    respx.get(f"{base}?_from=100&_to=149").mock(side_effect=lambda r: page(100, 119))

    async with httpx.AsyncClient() as client:
        scraper = ChangomasScraper(client=client, settings=settings)
        products = await scraper.fetch_term("arroz")

    assert len(products) == 120


@respx.mock
@pytest.mark.asyncio
async def test_fetch_term_stops_without_resources_header(settings) -> None:
    """Sin header `resources` no hay total: corta en la primera página."""
    _mock_robots()
    route = respx.get(
        "https://www.masonline.com.ar/api/catalog_system/pub/products/search/arroz"
        "?_from=0&_to=49"
    ).mock(return_value=httpx.Response(200, json=[_item("1")]))

    async with httpx.AsyncClient() as client:
        scraper = ChangomasScraper(client=client, settings=settings)
        products = await scraper.fetch_term("arroz")

    assert len(products) == 1
    assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_term_url_is_quoted(settings) -> None:
    _mock_robots()
    route = respx.get(
        "https://www.masonline.com.ar/api/catalog_system/pub/products/search/"
        "l%C3%A1cteos?_from=0&_to=49"
    ).mock(return_value=httpx.Response(200, json=[], headers={"resources": "0-49/0"}))

    async with httpx.AsyncClient() as client:
        scraper = ChangomasScraper(client=client, settings=settings)
        await scraper.fetch_term("lácteos")

    assert route.called is True


@respx.mock
@pytest.mark.asyncio
async def test_scrape_catalog_uses_configured_terms(settings) -> None:
    _mock_robots()
    route = respx.get(
        "https://www.masonline.com.ar/api/catalog_system/pub/products/search/arroz"
        "?_from=0&_to=49"
    ).mock(
        return_value=httpx.Response(
            206, json=[_item("1")], headers={"resources": "0-49/50"}
        )
    )

    async with httpx.AsyncClient() as client:
        scraper = ChangomasScraper(client=client, settings=settings)
        await scraper.scrape_catalog()

    assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_fetch_term_keeps_paging_when_page_has_no_stock(settings) -> None:
    """Una página entera sin stock NO es el fin del catálogo.

    Si el corte mirara los productos parseados en vez de la página cruda, se
    perderían los 50 productos siguientes.
    """
    _mock_robots()
    base = "https://www.masonline.com.ar/api/catalog_system/pub/products/search/arroz"

    def out_of_stock(start: int, end: int) -> list[dict[str, Any]]:
        page = []
        for i in range(end - start + 1):
            item = _item(str(start + i))
            item["items"][0]["sellers"][0]["commertialOffer"]["IsAvailable"] = False
            page.append(item)
        return page

    first = respx.get(f"{base}?_from=0&_to=49").mock(
        return_value=httpx.Response(
            206, json=out_of_stock(0, 49), headers={"resources": "0-49/100"}
        )
    )
    second = respx.get(f"{base}?_from=50&_to=99").mock(
        return_value=httpx.Response(
            206, json=[_item("99")], headers={"resources": "50-99/100"}
        )
    )

    async with httpx.AsyncClient() as client:
        scraper = ChangomasScraper(client=client, settings=settings)
        products = await scraper.fetch_term("arroz")

    assert first.call_count == 1
    assert second.call_count == 1
    assert [p.external_id for p in products] == ["99"]


@respx.mock
@pytest.mark.asyncio
async def test_scrape_catalog_dedupes_products_across_terms(settings) -> None:
    """`arroz` y `leche` devuelven el mismo producto ("Arroz con Leche").

    Sin deduplicar por `external_id`, el runner insertaría dos price_points del
    mismo precio con timestamps distintos.
    """
    _mock_robots()
    base = "https://www.masonline.com.ar/api/catalog_system/pub/products/search"
    respx.get(f"{base}/arroz?_from=0&_to=49").mock(
        return_value=httpx.Response(
            206,
            json=[_item("1"), _item("2")],
            headers={"resources": "0-49/2"},
        )
    )
    respx.get(f"{base}/leche?_from=0&_to=49").mock(
        return_value=httpx.Response(
            206,
            json=[_item("2"), _item("3")],
            headers={"resources": "0-49/2"},
        )
    )
    settings.changomas_terms = "arroz,leche"

    async with httpx.AsyncClient() as client:
        scraper = ChangomasScraper(client=client, settings=settings)
        products = await scraper.scrape_catalog()

    assert [p.external_id for p in products] == ["1", "2", "3"]


@respx.mock
@pytest.mark.asyncio
async def test_api_route_is_not_blocked_by_robots(settings) -> None:
    """Guarda contra el día que masonline agregue `/api/` al `Disallow`."""
    _mock_robots()
    route = respx.get(
        "https://www.masonline.com.ar/api/catalog_system/pub/products/search/arroz"
        "?_from=0&_to=49"
    ).mock(return_value=httpx.Response(200, json=[], headers={"resources": "0-49/0"}))

    async with httpx.AsyncClient() as client:
        scraper = ChangomasScraper(client=client, settings=settings)
        await scraper.fetch_term("arroz")

    assert route.called is True
