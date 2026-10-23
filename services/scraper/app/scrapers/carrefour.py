"""Scraper de Carrefour Argentina (VTEX).

Investigación (2026-09-24): el sitio es server-rendered (HTML de ~3.7 MB) pero
expone la API pública de VTEX ``catalog_system`` (robots.txt PERMITE
``/api/catalog_system``):

- ``GET https://www.carrefour.com.ar/api/catalog_system/pub/products/search/
  {categoria-path}?_from=0&_to=24`` -> JSON array de productos.
- El total viene en el header ``resources: 0-24/75`` (paginación en bloques de
  25, ``_from``/``_to`` inclusivos).
- Precio vigente: ``items[0].sellers[0].commertialOffer`` -> ``Price`` (venta),
  ``ListPrice`` (regular), ``PriceWithoutDiscount``, ``AvailableQuantity``,
  ``IsAvailable``. Si ``ListPrice > Price`` hay promo; se descartan productos
  sin stock (``IsAvailable == false`` o ``AvailableQuantity == 0``).
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from httpx import AsyncClient

from app.config import Settings
from app.models import RawProduct
from app.scrapers.base import BaseScraper
from app.scrapers.circuit_breaker import CircuitBreaker
from app.scrapers.robots import RobotsEnforcer

RESOURCES_RE = r"(\d+)-(\d+)/(\d+)"
DEFAULT_PAGE_SIZE = 25
RESOURCES_PATTERN = "resources"


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _promo_from_offer(offer: dict[str, Any]) -> tuple[str | None, Decimal | None]:
    """Detecta promo VTEX (ListPrice > Price) y calcula % de descuento."""
    price = _decimal(offer.get("Price"))
    list_price = _decimal(offer.get("ListPrice"))
    if price is None or list_price is None or list_price <= price or price <= 0:
        return None, None
    pct = int(round((list_price - price) / list_price * 100))
    return f"{pct}%_off", price


def parse_catalog(
    items: list[dict[str, Any]], *, captured_at: datetime | None = None
) -> list[RawProduct]:
    stamp = captured_at or datetime.now(UTC)
    products: list[RawProduct] = []
    for item in items:
        product_id = str(item.get("productId") or "")
        name = str(item.get("productName") or "").strip()
        if not product_id or not name:
            continue
        first_sku = (item.get("items") or [{}])[0]
        offer: dict[str, Any] = {}
        sellers = first_sku.get("sellers") or []
        if sellers:
            offer = sellers[0].get("commertialOffer") or {}
        if not offer.get("IsAvailable", False) or offer.get("AvailableQuantity", 0) <= 0:
            continue
        price = _decimal(offer.get("Price"))
        if price is None:
            continue
        promo_type, _promo_price = _promo_from_offer(offer)
        categories = item.get("categories") or []
        category_path = (
            str(categories[0]).strip("/").replace("/", " > ") if categories else None
        )
        products.append(
            RawProduct(
                external_id=product_id,
                name=name,
                url=str(item.get("link") or ""),
                price=price,
                promo_type=promo_type,
                category_path=category_path,
                captured_at=stamp,
            )
        )
    return products


def _parse_total(headers: Any) -> int | None:
    """Lee el total de productos del header ``resources: 0-24/75``."""
    import re

    value = headers.get(RESOURCES_PATTERN) if hasattr(headers, "get") else None
    if not value:
        return None
    match = re.search(RESOURCES_RE, str(value))
    return int(match.group(3)) if match else None


class CarrefourScraper(BaseScraper):
    def __init__(self, *, client: AsyncClient, settings: Settings) -> None:
        enforcer = RobotsEnforcer(
            client,
            user_agent=settings.scraper_user_agent,
            strict_on_failure=settings.robots_strict_on_failure,
            cache_ttl_seconds=settings.robots_cache_ttl_seconds,
        )
        super().__init__(
            client=client,
            enforcer=enforcer,
            circuit_breaker=CircuitBreaker(
                failure_threshold=settings.scraper_circuit_failure_threshold,
                reset_timeout_seconds=settings.scraper_circuit_reset_timeout,
            ),
            retries=settings.scraper_retries,
            min_interval=settings.scraper_min_request_interval,
        )
        self._settings = settings

    def _category_url(self, path: str, start: int) -> str:
        end = start + DEFAULT_PAGE_SIZE - 1
        return f"{self._settings.carrefour_api_base}/{path}?_from={start}&_to={end}"

    async def fetch_category(self, path: str) -> list[RawProduct]:
        products: list[RawProduct] = []
        start = 0
        total: int | None = None
        while total is None or start < total:
            response = await self.get(self._category_url(path, start))
            if total is None:
                total = _parse_total(response.headers)
            parsed = parse_catalog(response.json())
            products.extend(parsed)
            start += DEFAULT_PAGE_SIZE
            if total is None or start >= total:
                break
        return products

    async def scrape_catalog(self) -> list[RawProduct]:
        products: list[RawProduct] = []
        for path in self._settings.carrefour_paths:
            products.extend(await self.fetch_category(path))
        return products