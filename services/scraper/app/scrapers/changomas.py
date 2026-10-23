"""Scraper de ChangoMás / MAS Online (VTEX catalog_system).

Investigación real (2026-10-01) contra el sitio, no contra supuestos:

- ``changomas.com.ar`` **redirige a ``masonline.com.ar``**. Es el mismo backend
  VTEX que Carrefour pero otro catálogo (37.681 productos) y otro robots.txt.
- La API pública es ``GET {base}/products/search/{termino}?_from=N&_to=M`` ->
  JSON array de productos, con el total en el header ``resources: 0-49/37681``
  (VTEX devuelve 206 Partial Content). Página de 50 comprobada; con ``_to`` mayor
  a 50 la respuesta vuelve a 400 cerca del final del catálogo.
- **Tres cosas que NO funcionan y están documentadas acá porque son la trampa
  obvia al tocar este archivo:**
  1. ``?q={termino}`` devuelve siempre la página 1 del catálogo completo (los
     37.681), no filtra. Es un parámetro ignorado por esta VTEX.
  2. ``products/search/{slug-de-categoria}`` devuelve ``[]``. Las categorías del
     árbol cuelgan de ids internos (ej. ``Aceites, Vinagres Y Aderezos``) que la
     ruta de búsqueda no acepta; el path espera el término, no la categoría.
  3. ``products/search?f=0`` y ``?categoryId=N`` devuelven la página 1 completa
     del catálogo, no la categoría pedida.
  La única forma de acotar es el **path de término**, que además pagina bien.
- robots.txt de `masonline.com.ar` bloquea ``/img/*``, ``/account/*``,
  ``/login/*``, ``/checkout/*``, ``/busca/*``, ``/buscapagina/*``,
  ``/quick-view/*``, ``/espiar/*``, ``/metodos-de-pago/*`` y —para
  ``GPTBot``/``CCBot``/``anthropic-ai``/``Bytespider``— **todo el sitio**. Este
  adaptador NO usa ninguna de esas rutas: va por ``/api/catalog_system``, que no
  está en el ``Disallow`` (mismo criterio que el adaptador de Carrefour).
- Precio vigente: ``items[0].sellers[0].commertialOffer`` -> ``Price`` (venta),
  ``ListPrice`` (regular). Si ``ListPrice > Price`` hay promo y sale el % de
  descuento. Se descartan los productos sin stock o sin precio.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from httpx import AsyncClient

from app.config import Settings
from app.models import RawProduct
from app.scrapers.base import BaseScraper
from app.scrapers.circuit_breaker import CircuitBreaker
from app.scrapers.robots import RobotsEnforcer

RESOURCES_RE = re.compile(r"(\d+)-(\d+)/(\d+)")


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _promo_from_offer(offer: dict[str, Any]) -> tuple[str | None, Decimal | None]:
    """Promo VTEX (ListPrice > Price) con el % de descuento."""
    price = _decimal(offer.get("Price"))
    list_price = _decimal(offer.get("ListPrice"))
    if price is None or list_price is None or list_price <= price or price <= 0:
        return None, None
    pct = int(round((list_price - price) / list_price * 100))
    return f"{pct}%_off", price


def parse_catalog(
    items: list[dict[str, Any]], *, captured_at: datetime | None = None
) -> list[RawProduct]:
    """Convierte la respuesta de ``products/search`` en productos crudos."""
    stamp = captured_at or datetime.now(UTC)
    products: list[RawProduct] = []
    for item in items:
        product_id = str(item.get("productId") or "")
        name = str(item.get("productName") or "").strip()
        if not product_id or not name:
            continue
        skus = item.get("items") or [{}]
        first_sku = skus[0]
        sellers = first_sku.get("sellers") or []
        offer: dict[str, Any] = sellers[0].get("commertialOffer") or {} if sellers else {}
        if not offer.get("IsAvailable", False) or offer.get("AvailableQuantity", 0) <= 0:
            continue
        price = _decimal(offer.get("Price"))
        if price is None or price <= 0:
            continue
        promo_type, _promo_price = _promo_from_offer(offer)
        categories = item.get("categories") or []
        category_path = str(categories[0]).strip("/").replace("/", " > ") if categories else None
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


def parse_total(headers: Any) -> int | None:
    """Total de resultados desde el header ``resources: 0-49/37681``."""
    value = headers.get("resources") if hasattr(headers, "get") else None
    if not value:
        return None
    match = RESOURCES_RE.search(str(value))
    return int(match.group(3)) if match else None


class ChangomasScraper(BaseScraper):
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

    def _term_url(self, term: str, start: int) -> str:
        end = start + self._settings.changomas_results_per_page - 1
        return f"{self._settings.changomas_api_base}/{quote(term)}?_from={start}&_to={end}"

    async def fetch_term(self, term: str) -> list[RawProduct]:
        products: list[RawProduct] = []
        start = 0
        total: int | None = None
        page = self._settings.changomas_results_per_page
        while total is None or start < total:
            response = await self.get(self._term_url(term, start))
            if total is None:
                total = parse_total(response.headers)
            payload = response.json()
            products.extend(parse_catalog(payload))
            start += page
            # El corte mira la página *cruda*, no los productos parseados: una
            # página entera sin stock (todos `IsAvailable: false`) no significa
            # que se terminó el catálogo. Sin `resources` no hay forma de saber
            # si hay más, así que ahí sí se corta: un infinite-scroll sin total
            # sería peor que un scrape parcial explícito.
            if total is None or not payload or start >= total:
                break
        return products

    async def scrape_catalog(self) -> list[RawProduct]:
        """Recorre los términos configurados deduplicando por ``external_id``.

        Los términos se solapan: buscar "arroz" devuelve fideos y buscar "leche"
        devuelve "Arroz con Leche". Sin deduplicar, el mismo producto entra dos
        veces al runner y genera dos ``price_points`` del mismo precio (el hash
        incluye el timestamp) — además de inflar ``products_found``.
        """
        products: list[RawProduct] = []
        seen: set[str] = set()
        for term in self._settings.changomas_search_terms:
            for product in await self.fetch_term(term):
                if product.external_id in seen:
                    continue
                seen.add(product.external_id)
                products.append(product)
        return products
