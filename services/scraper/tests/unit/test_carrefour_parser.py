"""Tests del parser de Carrefour (fixture JSON real del API catalog_system)."""
from decimal import Decimal

from app.scrapers.carrefour import parse_catalog


def test_parses_available_products(carrefour_aceites) -> None:
    products = parse_catalog(carrefour_aceites)
    assert len(products) == 23


def test_name_external_id_and_price(carrefour_aceites) -> None:
    by_id = {p.external_id: p for p in parse_catalog(carrefour_aceites)}
    product = by_id["680457"]
    assert product.name == "Aceite de girasol alto omega Carrefour Classic pet 1.5 lts"
    assert product.price == Decimal("5405")
    assert "680457" in product.url
    assert product.promo_type == "6%_off"


def test_no_stock_product_skipped(carrefour_aceites) -> None:
    products = parse_catalog(carrefour_aceites)
    assert "576821" not in {p.external_id for p in products}


def test_no_promo_keeps_regular(carrefour_aceites) -> None:
    by_id = {p.external_id: p for p in parse_catalog(carrefour_aceites)}
    product = by_id["6307"]
    assert product.price == Decimal("6925")
    assert product.promo_type is None


def test_categories_path_from_categories(carrefour_aceites) -> None:
    by_id = {p.external_id: p for p in parse_catalog(carrefour_aceites)}
    product = by_id["680457"]
    assert "Aceites comunes" in (product.category_path or "")


def test_unknown_payload_returns_empty() -> None:
    assert parse_catalog([]) == []