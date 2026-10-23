"""Parser y scraper de Supermercados La Anónima (entre_rios).

Basado en la estructura real capturada (2026-09-23), ver investigación en
``docs/laanonima-structure.md``:

- Listados ``/{slug}/n{nivel}_{id}/`` con TODAS las tarjetas server-rendered
  en ``#productos-container`` (sin paginación). En cada tarjeta: JSON-LD
  ``ItemList`` con el precio regular (lista) + atributos ``data-*`` con
  precio PLUS / descuento / ruta de categorías.
- Detalle ``/{slug}/art_{id}/`` con JSON-LD ``Product``.
- robots.txt DESALOJA ``/buscar`` y ``/paginas/`` -> este adaptador NO usa la
  búsqueda del sitio; sólo recorre categorías públicas del sitemap.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from bs4 import BeautifulSoup
from httpx import AsyncClient

from app.config import Settings
from app.models import RawProduct
from app.scrapers.base import BaseScraper
from app.scrapers.circuit_breaker import CircuitBreaker
from app.scrapers.robots import RobotsEnforcer

JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_PROMO_RE = re.compile(r"promocion(\d+)-off", re.IGNORECASE)


def _find_item_list_blobs(html: str) -> list[dict[str, Any]]:
    """Extrae los bloques JSON-LD ``@type == ItemList`` del HTML."""
    blobs: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("@type") in ("ItemList", ["ItemList"]) or node.get("@type") == "ItemList":
                blobs.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for match in JSONLD_RE.finditer(html):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        walk(data)
    return blobs


def _parse_jsonld(html: str) -> dict[str, RawProduct]:
    """Productos de lista vía JSON-LD; la clave es la URL canónica."""
    products: dict[str, RawProduct] = {}
    for blob in _find_item_list_blobs(html):
        for element in blob.get("itemListElement", []):
            item = element.get("item") if isinstance(element, dict) else None
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            url = item.get("url")
            offers_raw = item.get("offers")
            offers = offers_raw if isinstance(offers_raw, dict) else {}
            price = offers.get("price") if isinstance(offers, dict) else None
            if not name or not url or price is None:
                continue
            try:
                price_dec = Decimal(str(price))
            except Exception:
                continue
            products[url] = RawProduct(
                external_id=_external_id(url),
                name=str(name).strip(),
                url=url,
                price=price_dec,
                captured_at=datetime.now(UTC),
            )
    return products


def _parse_cards(html: str) -> dict[str, dict[str, Any]]:
    """Datos de tarjeta (PLUS, descuento, categorías) por external_id.

    La tarjeta tiene href RELATIVO (``/slug/art_123/``) mientras que el
    JSON-LD usa URL absoluta; la clave compartida es el id del artículo.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards: dict[str, dict[str, Any]] = {}
    for anchor in soup.select("a[data-precio], a[data-nombre][href]"):
        href = str(anchor.get("href", "") or "")
        external_id = _external_id(href)
        if not external_id:
            continue
        data: dict[str, Any] = {}
        data["category_path"] = anchor.get("data-rutacategorias")
        promo_span = anchor.select_one("span.position.icono[class*='promocion']")
        classes: list[str] = []
        if promo_span:
            raw: Any = promo_span.attrs.get("class") or []
            classes = [str(cls) for cls in raw if isinstance(cls, str)]
        for cls in classes:
            m = _PROMO_RE.search(cls)
            if m:
                data["promo_type"] = f"{m.group(1)}%_off"
                break
        plus = anchor.select_one("div.precio.plus span")
        if plus:
            data["promo_price"] = _parse_price_text(plus.get_text("", strip=True))
        cards[external_id] = data
    return cards


def _parse_price_text(text: str) -> Decimal | None:
    cleaned = re.sub(r"[^\d,]", "", text).replace(".", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _external_id(url: str) -> str:
    m = re.search(r"/art_(\d+)/", url)
    return m.group(1) if m else ""


def parse_listing(html: str, *, captured_at: datetime | None = None) -> list[RawProduct]:
    """Convierte un listado de La Anónima en productos crudos.

    Fuente primaria: JSON-LD ItemList (precio regular/listado). La tarjeta
    agrega promo PLUS y ruta de categorías para el raw_json.
    """
    stamp = captured_at or datetime.now(UTC)
    products = _parse_jsonld(html)
    cards = _parse_cards(html)
    for product in products.values():
        product.captured_at = stamp
        extra = cards.get(product.external_id, {})
        if extra:
            product.promo_type = extra.get("promo_type")
            product.promo_price = extra.get("promo_price")
            product.category_path = extra.get("category_path")
    return list(products.values())


class LaAnonimaScraper(BaseScraper):
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

    async def fetch_category(self, url: str) -> list[RawProduct]:
        response = await self.get(url)
        return parse_listing(response.text)

    async def scrape_catalog(self) -> list[RawProduct]:
        products: list[RawProduct] = []
        for url in self._settings.category_urls:
            products.extend(await self.fetch_category(url))
        return products