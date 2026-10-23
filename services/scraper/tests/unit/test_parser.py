"""Tests del parser de listados de La Anónima (fixtures del HTML real)."""
from decimal import Decimal

from app.scrapers.la_anonima import parse_listing


def test_parses_all_products_from_itemlist(aceite_html: str) -> None:
    products = parse_listing(aceite_html)
    assert len(products) == 8


def test_name_url_and_price_extracted(aceite_html: str) -> None:
    by_url = {p.url: p for p in parse_listing(aceite_html)}
    url = "https://www.laanonima.com.ar/aceite-de-girasol-natura-x-900-cc/art_1559369/"
    product = by_url[url]
    assert product.name == "Aceite de Girasol Natura x 900 cc."
    assert str(product.price) == "4750"
    assert product.external_id == "1559369"


def test_promo_percent_and_plus_price_from_card(aceite_html: str) -> None:
    by_url = {p.url: p for p in parse_listing(aceite_html)}
    url = "https://www.laanonima.com.ar/aceite-de-oliva-extra-virgen-suave-natura-oliva-x-500-cc/art_3129986/"
    product = by_url[url]
    assert product.promo_type == "25%_off"
    assert Decimal(product.promo_price or 0) == Decimal("11400")
    assert "Aceite, Aderezos y Condimentos" in (product.category_path or "")


def test_product_without_promo_span_stays_regular(aceite_html: str) -> None:
    by_url = {p.url: p for p in parse_listing(aceite_html)}
    url = "https://www.laanonima.com.ar/aceite-de-girasol-la-anonima-x-900-cc/art_2438409/"
    product = by_url[url]
    assert product.promo_type is None
    assert str(product.price) == "5350"


def test_price_with_leading_zeros_parsed(harina_html: str) -> None:
    products = parse_listing(harina_html)
    assert len(products) == 5
    morixe = next(p for p in products if p.external_id == "0107803")
    assert str(morixe.price) == "790"


def test_unknown_html_returns_empty() -> None:
    assert parse_listing("<html><body>sin productos</body></html>") == []