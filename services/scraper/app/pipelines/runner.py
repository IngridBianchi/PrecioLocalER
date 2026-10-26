"""Orquestación del scrape: fetch -> normalizar -> persistir."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.models import PricePointIn, ProductIn, ScrapeSummary, SkuIn
from app.pipelines.dedupe import compute_price_hash
from app.pipelines.normalize import extract_size, normalize_name

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "aceites": ("aceite",),
    "harinas": ("harina",),
    "azucar": ("azucar", "azúcar"),
    "arroces-y-pastas": ("arroz", "pasta", "fideo", "ñoqui"),
    "bebidas": ("bebida",),
    "gaseosas": ("gaseosa", "cola", "sprite", "fanta"),
    "aguas": ("agua",),
    "jugos": ("jugo",),
    "vinos": ("vino",),
    "cervezas": ("cerveza",),
    "lacteos": ("leche", "yogur", "yogurt", "crema"),
    "quesos": ("queso",),
    "manteca-y-crema": ("manteca",),
    "galletitas-y-copetin": ("galletita", "galleta", "copetin", "snack"),
    "dulces": ("dulce", "mermelada", "miel"),
    "conservas": ("conserva", "atun", "atún", "sardina"),
    "limpieza": ("limpieza", "limpiador"),
    "lavandina": ("lavandina", "cloro"),
    "detergentes": ("detergente", "jabon", "jabón"),
    "papel-higienico": ("papel higienico", "papel higiénico", "servilleta"),
    "shampoo-y-acondicionador": ("shampoo", "acondicionador"),
    "higiene-dental": ("dentifrico", "dentífrico", "cepillo dental", "enjuague bucal"),
    "panales-y-toallitas": ("panal", "toallita"),
    "bebes-y-cuidado": ("bebe", "bebé", "infantil"),
    "cuidado-personal": ("desodorante", "shampoo", "acondicionador"),
}


def _infer_category_slug(name: str) -> str | None:
    lowered = name.lower()
    for slug, keywords in _CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return slug
    return None

if TYPE_CHECKING:
    from app.scrapers.base import BaseScraper
    from app.storage.postgres import ProductRepository, RedisReadiness


class ScrapeRunner:
    def __init__(
        self,
        scraper: BaseScraper,
        repository: ProductRepository,
        chain: str,
        redis: RedisReadiness | None = None,
    ) -> None:
        self._scraper = scraper
        self._repo = repository
        self._chain = chain
        self._redis = redis

    async def run(self) -> ScrapeSummary:
        summary = ScrapeSummary(chain=self._chain)
        chain_id = await self._repo.chain_id(self._chain)
        if chain_id is None:
            raise LookupError(f"cadena '{self._chain}' no seedeada en la DB")

        # Marca de agua del scrape: los SKU con last_seen_at anterior a este
        # instante son los que no se vieron en esta pasada (soft-delete, T8).
        scrape_started_at = datetime.now(UTC)

        raw_products = await self._scraper.scrape_catalog()
        summary.products_found = len(raw_products)

        for raw in raw_products:
            try:
                size = extract_size(raw.name)
                normalized = normalize_name(raw.name)
                category_path = getattr(raw, "category_path", None)
                category_id = await self._repo.category_id_by_path(category_path)
                if category_id is None:
                    category_id = await self._repo.category_id_by_slug(
                        _infer_category_slug(raw.name)
                    )
                product_id = await self._repo.upsert_product(
                    ProductIn(
                        name=raw.name,
                        normalized_name=normalized,
                        size_quantity=size.quantity,
                        size_unit=size.unit,
                        category_id=category_id,
                    )
                )
                if product_id is None:
                    continue
                summary.products_upserted += 1

                sku_id = await self._repo.upsert_sku(
                    SkuIn(
                        product_id=product_id,
                        chain_id=chain_id,
                        external_id=raw.external_id,
                        name=raw.name,
                        url=raw.url,
                    )
                )
                if sku_id is None:
                    summary.errors += 1
                    continue

                captured_at = raw.captured_at or datetime.now(UTC)
                price_hash = compute_price_hash(
                    chain_id, sku_id, str(raw.price), captured_at.isoformat()
                )
                inserted, price_point_id = await self._repo.insert_price_point(
                    PricePointIn(
                        product_id=product_id,
                        chain_id=chain_id,
                        sku_id=sku_id,
                        store_id=None,
                        price=raw.price,
                        promo_type=raw.promo_type,
                        captured_at=captured_at,
                        raw_json=raw.model_dump(mode="json"),
                        hash=price_hash,
                    )
                )
                if inserted:
                    summary.price_points_inserted += 1
                    if self._redis is not None:
                        await self._redis.publish_price_event(
                            {
                                "id": price_point_id,
                                "productId": product_id,
                                "chain": self._chain,
                                "chainId": chain_id,
                                "skuId": sku_id,
                                "storeId": None,
                                "price": str(raw.price),
                                "promoType": raw.promo_type,
                                "capturedAt": captured_at.isoformat(),
                                "scrapedAt": datetime.now(UTC).isoformat(),
                                "hash": price_hash,
                            }
                        )
                else:
                    summary.price_points_skipped += 1
            except Exception:
                # Un producto no debe tirar abajo el lote completo.
                summary.errors += 1

        await self._deactivate_missing(chain_id, scrape_started_at, summary)
        return summary

    async def _deactivate_missing(
        self, chain_id: int, scrape_started_at: datetime, summary: ScrapeSummary
    ) -> None:
        """Apaga los SKU que la cadena ya no lista (deuda T8).

        Solo si el scrape terminó limpio. Un scrape parcial —una categoría
        que falló, un 500 a mitad de lote— no es evidencia de que el ítem
        desapareció de la góndola, así que en ese caso no se toca nada: es
        preferible mostrar un producto que se fue antes que borrar el precio
        de algo que sigue en el sitio.
        """
        if summary.errors:
            return
        try:
            summary.skus_deactivated = await self._repo.deactivate_missing_skus(
                chain_id, scrape_started_at
            )
        except Exception:
            summary.errors += 1