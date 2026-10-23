"""Tests del parser y del scraper de red de La Anónima.

Estructura que consume el parser (captura real, ver `docs/laanonima-structure.md`):
- JSON-LD `ItemList` con `itemListElement[].item` (no el producto directo).
- Tarjetas `a[data-precio]` / `a[data-nombre][href]` con href relativo
  `/slug/art_{id}/`, `data-rutacategorias` y `span.icono.promocion{N}-off`.
- Precio PLUS en `div.precio.plus span`.
"""
from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx
from app.config import Settings
from app.errors import CircuitOpenError, RobotsDeniedError, UpstreamError
from app.scrapers.la_anonima import (
    LaAnonimaScraper,
    _external_id,
    _parse_price_text,
    parse_listing,
)

ROBOTS = "User-agent: *\nAllow: /\nDisallow: /buscar\nDisallow: /paginas/\n"


def _jsonld(items: str) -> str:
    return f"""
    <script type="application/ld+json">
    {{
      "@context": "https://schema.org",
      "@type": "ItemList",
      "itemListElement": [{items}]
    }}
    </script>
    """


def _item(name: str, sku: str, price: str) -> str:
    return f"""{{
        "@type": "ListItem",
        "position": 1,
        "item": {{
          "@type": "Product",
          "name": "{name}",
          "url": "https://www.laanonima.com.ar/almacen/art_{sku}/",
          "offers": {{"@type": "Offer", "price": "{price}", "priceCurrency": "ARS"}}
        }}
      }}"""


def test_parse_price_text_ars_format() -> None:
    assert _parse_price_text("$1.234,56") == Decimal("1234.56")
    assert _parse_price_text("$ 2.500,00") == Decimal("2500.00")


def test_parse_price_text_without_value() -> None:
    assert _parse_price_text("sin precio") is None
    assert _parse_price_text("") is None


def test_external_id_extracted_from_relative_and_absolute() -> None:
    assert _external_id("/almacen/art_12345/") == "12345"
    assert _external_id("https://www.laanonima.com.ar/almacen/art_12345/?p=2") == "12345"


def test_external_id_empty_when_pattern_absent() -> None:
    """Sin id no se puede deduplicar contra la BD: `""` y se descarta."""
    assert _external_id("https://www.laanonima.com.ar/promociones") == ""


def _page(*items: str) -> str:
    """HTML mínimo con un listado JSON-LD de `items`."""
    return f"<html><head>{_jsonld(','.join(items))}</head><body></body></html>"


def test_parse_listing_jsonld_only() -> None:
    html = _page(_item("Aceite de Girasol", "12345", "1500"))
    products = parse_listing(html)
    assert len(products) == 1
    assert products[0].external_id == "12345"
    assert products[0].price == Decimal("1500")
    assert products[0].promo_type is None
    assert products[0].category_path is None


def test_parse_listing_skips_item_without_price() -> None:
    item = """{
        "@type": "ListItem",
        "item": {"@type": "Product", "name": "Sin precio",
                 "url": "https://www.laanonima.com.ar/almacen/art_9/"}
      }"""
    assert parse_listing(_page(item)) == []


def test_parse_listing_skips_item_without_url() -> None:
    item = """{
        "@type": "ListItem",
        "item": {"@type": "Product", "name": "Sin url", "offers": {"price": "100"}}
      }"""
    assert parse_listing(_page(item)) == []


def test_parse_listing_ignores_malformed_jsonld() -> None:
    """Un bloque roto no puede tumbar el listado completo."""
    html = """
    <html><head>
    <script type="application/ld+json">{ "@type": "ItemList", </script>
    </head><body></body></html>
    """
    assert parse_listing(html) == []


def test_parse_listing_card_adds_plus_promo_and_category() -> None:
    html = f"""
    <html>
    <head>{_jsonld(_item('Aceite de Girasol', '12345', '1500'))}</head>
    <body>
      <a data-nombre href="/almacen/art_12345/" data-precio="1500"
         data-rutacategorias="Almacen > Aceites">
        <span class="position icono promocion20-off"></span>
        <div class="precio"><span>$ 1.500</span></div>
        <div class="precio plus"><span>$ 1.200,00</span></div>
      </a>
    </body>
    </html>
    """
    product = parse_listing(html)[0]
    assert product.price == Decimal("1500")
    assert product.promo_type == "20%_off"
    assert product.promo_price == Decimal("1200.00")
    assert product.category_path == "Almacen > Aceites"


def test_parse_listing_card_without_promo_keeps_regular_price() -> None:
    html = f"""
    <html>
    <head>{_jsonld(_item('Harina 000', '777', '500'))}</head>
    <body>
      <a data-nombre href="/almacen/art_777/" data-precio="500"
         data-rutacategorias="Almacen > Harinas">
        <span class="position icono destacado"></span>
        <div class="precio"><span>$ 500</span></div>
      </a>
    </body>
    </html>
    """
    product = parse_listing(html)[0]
    assert product.promo_type is None
    assert product.promo_price is None
    assert product.category_path == "Almacen > Harinas"


def test_parse_listing_card_without_id_is_ignored() -> None:
    """Una tarjeta de portada sin `art_{id}` no corresponde a ningún producto."""
    html = f"""
    <html>
    <head>{_jsonld(_item('Aceite', '12345', '1500'))}</head>
    <body>
      <a data-nombre href="/promociones/" data-precio="999"
         data-rutacategorias="Ofertas">
        <span class="position icono promocion50-off"></span>
        <div class="precio plus"><span>$ 999</span></div>
      </a>
    </body>
    </html>
    """
    product = parse_listing(html)[0]
    assert product.external_id == "12345"
    assert product.promo_type is None


def test_parse_listing_captured_at_is_overridable() -> None:
    from datetime import UTC, datetime

    stamp = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    html = f"<html><head>{_jsonld(_item('Aceite', '12345', '1500'))}</head><body></body></html>"
    products = parse_listing(html, captured_at=stamp)
    assert products[0].captured_at == stamp


@pytest.fixture
def anon_settings() -> Settings:
    return Settings(
        app_environment="test",
        database_url="",
        redis_url="",
        scraper_min_request_interval=0,
        scraper_retries=1,
        la_anonima_categories="https://www.laanonima.com.ar/almacen/n1_1/,"
        "https://www.laanonima.com.ar/almacen/n2_2/",
    )


def _mock_robots(text: str = ROBOTS) -> None:
    respx.get("https://www.laanonima.com.ar/robots.txt").mock(
        return_value=httpx.Response(200, text=text)
    )


@respx.mock
@pytest.mark.asyncio
async def test_fetch_category_parses_listing(anon_settings: Settings) -> None:
    _mock_robots()
    respx.get("https://www.laanonima.com.ar/almacen/n1_1/").mock(
        return_value=httpx.Response(
            200, text=_page(_item("Aceite", "12345", "1500"))
        )
    )

    async with httpx.AsyncClient() as client:
        scraper = LaAnonimaScraper(client=client, settings=anon_settings)
        products = await scraper.fetch_category(
            "https://www.laanonima.com.ar/almacen/n1_1/"
        )

    assert [p.external_id for p in products] == ["12345"]


@respx.mock
@pytest.mark.asyncio
async def test_scrape_catalog_visits_every_configured_category(
    anon_settings: Settings,
) -> None:
    _mock_robots()
    first = respx.get("https://www.laanonima.com.ar/almacen/n1_1/").mock(
        return_value=httpx.Response(
            200,
            text=f"<html><head>{_jsonld(_item('Aceite', '1', '1500'))}</head><body></body></html>",
        )
    )
    second = respx.get("https://www.laanonima.com.ar/almacen/n2_2/").mock(
        return_value=httpx.Response(
            200,
            text=f"<html><head>{_jsonld(_item('Harina', '2', '500'))}</head><body></body></html>",
        )
    )

    async with httpx.AsyncClient() as client:
        scraper = LaAnonimaScraper(client=client, settings=anon_settings)
        products = await scraper.scrape_catalog()

    assert first.call_count == 1
    assert second.call_count == 1
    assert {p.external_id for p in products} == {"1", "2"}


@respx.mock
@pytest.mark.asyncio
async def test_robots_denial_is_not_retried(anon_settings: Settings) -> None:
    """`/buscar` está bloqueado: el scraper debe negarse sin reintentar."""
    _mock_robots()
    blocked = respx.get("https://www.laanonima.com.ar/buscar/").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )

    async with httpx.AsyncClient() as client:
        scraper = LaAnonimaScraper(client=client, settings=anon_settings)
        with pytest.raises(RobotsDeniedError):
            await scraper.fetch_category("https://www.laanonima.com.ar/buscar/")

    assert blocked.call_count == 0


@respx.mock
@pytest.mark.asyncio
async def test_robots_fetch_failure_is_fail_safe(anon_settings: Settings) -> None:
    """Sin robots.txt legible (`strict`) no se scrapea: fail-safe (ADR-0007)."""
    respx.get("https://www.laanonima.com.ar/robots.txt").mock(
        return_value=httpx.Response(503)
    )

    async with httpx.AsyncClient() as client:
        scraper = LaAnonimaScraper(client=client, settings=anon_settings)
        with pytest.raises(RobotsDeniedError):
            await scraper.fetch_category("https://www.laanonima.com.ar/almacen/n1_1/")


@respx.mock
@pytest.mark.asyncio
async def test_upstream_error_is_not_swallowed(anon_settings: Settings) -> None:
    """Un 500 debe propagar: devolver `[]` dispararía soft-delete de los SKU."""
    _mock_robots()
    respx.get("https://www.laanonima.com.ar/almacen/n1_1/").mock(
        return_value=httpx.Response(500)
    )

    async with httpx.AsyncClient() as client:
        scraper = LaAnonimaScraper(client=client, settings=anon_settings)
        with pytest.raises(UpstreamError):
            await scraper.scrape_catalog()


@respx.mock
@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_repeated_failures(
    anon_settings: Settings,
) -> None:
    """El circuito se arma por request fallido (incluidos los reintentos).

    Con `retries=1` un solo `fetch_category` agotado consume 2 fallos: con
    `threshold=2` el circuito ya queda abierto para el siguiente request.
    """
    anon_settings.scraper_circuit_failure_threshold = 2
    anon_settings.scraper_retries = 1
    _mock_robots()
    route = respx.get("https://www.laanonima.com.ar/almacen/n1_1/").mock(
        return_value=httpx.Response(500)
    )

    async with httpx.AsyncClient() as client:
        scraper = LaAnonimaScraper(client=client, settings=anon_settings)
        with pytest.raises(UpstreamError):
            await scraper.fetch_category("https://www.laanonima.com.ar/almacen/n1_1/")
        assert scraper.circuit_state == "open"
        with pytest.raises(CircuitOpenError):
            await scraper.fetch_category("https://www.laanonima.com.ar/almacen/n1_1/")

    # El circuito abierto corta la red: no se insiste contra el sitio.
    assert route.call_count == 2  # 1 intento + 1 retry, y nada más


@pytest.mark.asyncio
async def test_scrape_catalog_without_configured_urls() -> None:
    settings = Settings(
        app_environment="test",
        database_url="",
        redis_url="",
        scraper_min_request_interval=0,
        la_anonima_categories="",
    )
    async with httpx.AsyncClient() as client:
        scraper = LaAnonimaScraper(client=client, settings=settings)
        assert await scraper.scrape_catalog() == []
