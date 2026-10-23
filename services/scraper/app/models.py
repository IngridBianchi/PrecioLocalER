"""Modelos de dominio y contratos de API del scraper.

Entidades canónicas definidas en docs/contracts.md.
"""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Identidad de venta: sin estos tres campos un catálogo es basura y termina
# ocupando filas en `products` con normalized_name vacío. Que la validación
# esté en el modelo y no en el runner permite que el conteo de `errors` por
# producto del lote sea real (antes un nombre vacío pasaba el filtro y creaba
# un producto basura silenciosamente).
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PositiveAmount = Annotated[Decimal, Field(gt=0)]


class RawProduct(BaseModel):
    """Producto tal como sale del HTML (sin normalizar)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_id: NonEmptyStr
    name: NonEmptyStr
    url: NonEmptyStr
    price: PositiveAmount
    currency: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True)] = "ARS"
    promo_type: str | None = None
    promo_price: Decimal | None = None
    category_path: str | None = None
    captured_at: datetime


class ProductIn(BaseModel):
    """Producto normalizado listo para persistir.

    normalized_name es lo que permite el dedupe (unique index).
    """

    name: NonEmptyStr
    normalized_name: NonEmptyStr
    size_quantity: Decimal | None = None
    size_unit: str = "und"
    category_id: int | None = None


class SkuIn(BaseModel):
    """SKU (id de venta real de una cadena) de un producto canónico."""

    product_id: int
    chain_id: int
    external_id: NonEmptyStr
    name: NonEmptyStr
    url: str | None = None
    ean: str | None = None


class PricePointIn(BaseModel):
    # Los tres "nullable" llevan default explícito: en pydantic, `X | None` sin
    # default sigue siendo obligatorio, y hacer pasar `store_id=None` a mano
    # en cada llamada es ruido que invite a olvidarse (y a romper). `SkuIn` ya
    # lo hacía así; acá faltaba.
    product_id: int
    chain_id: int
    sku_id: int
    store_id: int | None = None
    price: PositiveAmount
    promo_type: str | None = None
    captured_at: datetime
    raw_json: dict[str, Any] = Field(default_factory=dict)
    hash: NonEmptyStr


class ProductOut(BaseModel):
    id: int
    name: str
    normalized_name: str
    brand: str | None = None
    category: str | None = None
    category_slug: str | None = None
    size_quantity: Decimal | None = None
    size_unit: str
    ean: str | None = None


class ChainOut(BaseModel):
    """Cadena activa. Alimenta el selector de cadena de la web (WP4)."""

    id: int
    slug: str
    name: str
    active: bool


class ProductSearchOut(BaseModel):
    """Resultado de búsqueda: producto + su precio vigente por cadena.

    Una fila por (producto, cadena), con el último price_point de esa cadena.
    """

    id: int
    name: str
    normalized_name: str
    brand: str | None = None
    category: str | None = None
    category_slug: str | None = None
    size_quantity: Decimal | None = None
    size_unit: str
    ean: str | None = None
    chain: str
    chain_name: str
    price: str
    promo_type: str | None = None
    captured_at: datetime


class ProductSearchPageOut(BaseModel):
    """Página de resultados de búsqueda con total de coincidencias."""
    products: list[ProductSearchOut]
    total: int


class PricePointOut(BaseModel):
    id: int
    product_id: int
    chain: str
    chain_id: int
    sku_id: int | None = None
    store_id: int | None
    price: str
    promo_type: str | None
    captured_at: datetime
    scraped_at: datetime
    hash: str


class CategoryPriceOut(BaseModel):
    """Precio vigente de un producto dentro de una categoría (reporte por categoría)."""

    id: int
    product_id: int
    product_name: str
    chain: str
    chain_id: int
    price: str
    promo_type: str | None = None
    captured_at: datetime


class ScrapeSummary(BaseModel):
    chain: str
    products_found: int = 0
    products_upserted: int = 0
    price_points_inserted: int = 0
    price_points_skipped: int = 0
    skus_deactivated: int = 0
    errors: int = 0


class ProductDetailOut(BaseModel):
    """Detalle de producto: metadatos + precio vigente por cadena."""

    id: int
    name: str
    normalized_name: str
    brand: str | None = None
    category: str | None = None
    category_slug: str | None = None
    size_quantity: Decimal | None = None
    size_unit: str
    ean: str | None = None
    prices: list[PricePointOut] = []


def money(value: Decimal | int | float | None) -> str:
    """Serializa precios como string decimal (contrato JSON, docs/contracts.md)."""
    if value is None:
        return ""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"{dec.normalize():f}" if dec == dec.to_integral_value() else f"{dec:.2f}"