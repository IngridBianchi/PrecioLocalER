"""Tests del parser de Coto Digital (fixture JSON real del BFF products/search)."""
from decimal import Decimal

from app.scrapers.coto_digital import parse_search


def test_parses_all_products(coto_aceite) -> None:
    products = parse_search(coto_aceite)
    assert len(products) == 17


def test_name_external_id_and_price(coto_aceite) -> None:
    by_sku = {p.external_id: p for p in parse_search(coto_aceite)}
    product = by_sku["11760"]
    assert product.name == "Aceite Girasol COCINERO Bot 1.5 Ltr"
    assert product.price == Decimal("6925")
    assert product.promo_type is None
    assert "productos/aceite-girasol-cocinero-bot-1-5-ltr" in product.url


def test_product_without_stock_200_is_skipped(coto_aceite) -> None:
    import copy

    payload = copy.deepcopy(coto_aceite)
    for result in payload["response"]["results"]:
        result["data"]["price"] = [p for p in result["data"]["price"] if p["store"] != "200"]
    assert parse_search(payload) == []


def test_promo_discount_parsed(coto_aceite) -> None:
    by_sku = {p.external_id: p for p in parse_search(coto_aceite)}
    product = by_sku["221684"]
    assert product.promo_type == "25%_off"
    assert product.promo_price == Decimal("4132.50")


def test_unknown_payload_returns_empty() -> None:
    assert parse_search({"response": {"results": []}}) == []