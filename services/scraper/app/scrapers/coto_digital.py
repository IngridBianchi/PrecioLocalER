"""Scraper de Coto Digital (SPA Angular + BFF Constructor.io).

Investigación (2026-09-24) en ``docs/`` y pruéboses en TEMP:
``api/coto_digital.md`` si existe; estructura en ``coto_main.js`` (bundle):

- El sitio renderiza una SPA (HTML de 4475 bytes); la data llega del BFF
  público ``https://api.coto.com.ar/api/v1/ms-digital-sitio-bff-web/api/v1``:
  ``GET /products/search/{term}?key=...&num_results_per_page=N&
  pre_filter_expression={"name":"store_availability","value":"200"}&us=200``
  con paginación ``page=N`` + ``offset=N``.
- robots.txt: ``api.coto.com.ar`` NO tiene robots.txt propio (404); el del
  sitio principal (www.cotodigital.com.ar) está cubierto por alias en
  ``RobotsEnforcer`` (ADR-0007: no eludir medidas técnicas, volumen bajo).
- Precio vigente: ``data.price[]`` con ``store == "200"`` -> ``listPrice``
  (ARS entero). ``data.discounts[]`` vacío = sin promo; si hay promo,
  ``discountText`` tipo ``"25%Dto"`` y ``discountPrice`` "$4132.50".
- URL canónica de producto (sitemap productos.xml): ``.../productos/{slug}/_/
  R-{sku}-{sku}-200`` -- ``data.url`` es ``_/R-...``; el slug sale del nombre.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from httpx import AsyncClient

from app.config import Settings
from app.errors import UpstreamError
from app.models import RawProduct
from app.pipelines.normalize import strip_accents
from app.scrapers.base import BaseScraper
from app.scrapers.circuit_breaker import CircuitBreaker
from app.scrapers.robots import RobotsEnforcer

_DISCOUNT_RE = re.compile(r"(\d+)\s*%", re.IGNORECASE)
_MONEY_RE = re.compile(r"\$?\s*([\d.,]+)")

API_ORIGIN = "https://api.coto.com.ar"
SITE_ORIGIN = "https://www.cotodigital.com.ar"


def _parse_ars(text: Any) -> Decimal | None:
    """Convierte 5510, "$4132.50" o "1.234,56" a Decimal.

    El BFF mezcla formatos: ``listPrice`` viene como entero JSON (5510) y
    ``discountPrice`` como string con puntos decimales ("$4132.50"). La regla:
    si hay coma se toma como separador decimal (formato AR) y el punto como
    miles; si no hay coma, el punto es el separador decimal (formato US).
    """
    if text is None:
        return None
    raw = str(text).strip()
    match = _MONEY_RE.search(raw)
    if not match:
        return None
    number = match.group(1)
    if "," in number:
        number = number.replace(".", "").replace(",", ".")
    try:
        return Decimal(number)
    except InvalidOperation:
        return None


def _discount_to_promo(data: dict[str, Any]) -> tuple[str | None, Decimal | None]:
    """Promo del primer descuento: (promo_type, promo_price)."""
    discounts = data.get("discounts") or []
    if not discounts:
        return None, None
    first = discounts[0] if isinstance(discounts, list) else discounts
    if not isinstance(first, dict):
        return None, None
    text = str(first.get("discountText") or "")
    promo_type: str | None = None
    match = _DISCOUNT_RE.search(text)
    if match:
        promo_type = f"{match.group(1)}%_off"
    return promo_type, _parse_ars(first.get("discountPrice"))


def _product_url(data: dict[str, Any]) -> str:
    relative = str(data.get("url") or "")
    if not relative.startswith("_/R-"):
        return relative
    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        strip_accents(str(data.get("sku_description") or "")).lower(),
    ).strip("-")
    return f"{SITE_ORIGIN}/sitios/cdigi/productos/{slug}/{relative}"


def parse_search(
    payload: dict[str, Any], *, captured_at: datetime | None = None
) -> list[RawProduct]:
    """Convierte la respuesta de ``products/search`` en productos crudos.

    Filtra el precio de la sucursal digital (store "200"); si el producto no
    tiene precio para esa sucursal se descarta.
    """
    stamp = captured_at or datetime.now(UTC)
    response = payload.get("response") or {}
    products: list[RawProduct] = []
    for result in response.get("results") or []:
        data = result.get("data") or {}
        sku = str(data.get("sku_plu") or "")
        name = str(data.get("sku_description") or "").strip()
        if not sku or not name:
            continue
        price_entry: dict[str, Any] | None = None
        for entry in data.get("price") or []:
            if str(entry.get("store")) == "200":
                price_entry = entry
                break
        price = _parse_ars(price_entry.get("listPrice") if price_entry else None)
        if price is None:
            continue
        promo_type, promo_price = _discount_to_promo(data)
        products.append(
            RawProduct(
                external_id=sku,
                name=name,
                url=_product_url(data),
                price=price,
                promo_type=promo_type,
                promo_price=promo_price,
                captured_at=stamp,
            )
        )
    return products


class CotoDigitalScraper(BaseScraper):
    def __init__(self, *, client: AsyncClient, settings: Settings) -> None:
        enforcer = RobotsEnforcer(
            client,
            user_agent=settings.scraper_user_agent,
            strict_on_failure=settings.robots_strict_on_failure,
            cache_ttl_seconds=settings.robots_cache_ttl_seconds,
            aliases={API_ORIGIN: SITE_ORIGIN},
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

    def _search_url(self, term: str, page: int, per_page: int) -> str:
        import urllib.parse

        key = self._settings.coto_digital_api_key
        if not key:
            raise UpstreamError("COTO_DIGITAL_API_KEY no configurada (fail-closed)")
        params = urllib.parse.urlencode(
            {
                "key": key,
                "num_results_per_page": str(per_page),
                "pre_filter_expression": '{"name":"store_availability","value":"200"}',
                "us": "200",
                "page": str(page),
                "offset": str((page - 1) * per_page),
            }
        )
        quoted = urllib.parse.quote(term)
        return f"{self._settings.coto_digital_api_base}/products/search/{quoted}?{params}"

    async def fetch_term(self, term: str) -> list[RawProduct]:
        per_page = self._settings.coto_digital_results_per_page
        products: list[RawProduct] = []
        page = 1
        while True:
            response = await self.get(self._search_url(term, page, per_page))
            payload = response.json()
            parsed = parse_search(payload)
            products.extend(parsed)
            total = int(payload.get("response", {}).get("total_num_results") or len(parsed))
            if len(products) >= total or page * per_page >= total or not parsed:
                break
            page += 1
        return products

    async def scrape_catalog(self) -> list[RawProduct]:
        products: list[RawProduct] = []
        for term in self._settings.coto_digital_terms:
            products.extend(await self.fetch_term(term))
        return products