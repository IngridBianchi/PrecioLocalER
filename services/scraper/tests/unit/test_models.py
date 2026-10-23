"""Tests del contrato de `RawProduct`: qué acepta entrar al pipeline.

Un scraper mal escrito no debe poder meter basura en `products`: si el nombre
viene vacío, `normalized_name` queda en "" y crea una fila inútil que después
contamina la búsqueda. La validación vive en el modelo para que además el
runner pueda contarla en `ScrapeSummary.errors` y seguir con el resto del lote.
"""
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.models import ProductIn, RawProduct
from pydantic import ValidationError

_VALID = {
    "external_id": "531487",
    "name": "Harina De Trigo Chacabuco Paquete 750 Gr",
    "url": "https://www.tienda.com/p/531487",
    "price": Decimal("2129.00"),
    "captured_at": datetime.now(UTC),
}


def _raw(**overrides: object) -> RawProduct:
    return RawProduct(**{**_VALID, **overrides})  # type: ignore[arg-type]


def test_valid_product_is_accepted() -> None:
    raw = _raw()
    assert raw.currency == "ARS"
    assert raw.promo_type is None


# --- Identidad: los tres campos que definen una venta -----------------------


@pytest.mark.parametrize("field", ["external_id", "name", "url"])
def test_blank_identity_field_is_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        _raw(**{field: ""})


@pytest.mark.parametrize("field", ["external_id", "name", "url"])
def test_whitespace_only_identity_field_is_rejected(field: str) -> None:
    """Un scraper que parsea un selector inexistente suele devolver " "."""
    with pytest.raises(ValidationError):
        _raw(**{field: "   "})


@pytest.mark.parametrize("field", ["external_id", "name", "url"])
def test_surrounding_whitespace_is_trimmed(field: str) -> None:
    raw = _raw(**{field: f"  {_VALID[field]}  "})  # type: ignore[literal-required]
    assert getattr(raw, field) == _VALID[field]


# --- Precio ------------------------------------------------------------------


@pytest.mark.parametrize("price", ["0", "-1", "-0.01"])
def test_non_positive_price_is_rejected(price: str) -> None:
    with pytest.raises(ValidationError):
        _raw(price=Decimal(price))


def test_decimal_price_is_preserved_exactly() -> None:
    """Nada de float: 2129.10 tiene que seguir siendo 2129.10, no 2129.0999."""
    assert _raw(price=Decimal("2129.10")).price == Decimal("2129.10")


# --- Moneda ------------------------------------------------------------------


def test_currency_is_uppercased() -> None:
    assert _raw(currency="ars").currency == "ARS"


# --- ProductIn: lo que realmente se escribe en la base -----------------------


def test_product_in_rejects_blank_normalized_name() -> None:
    """Es la clave del unique index de dedupe: vacía no puede pasar."""
    with pytest.raises(ValidationError):
        ProductIn(name="Algo", normalized_name="")


def test_product_in_rejects_blank_name() -> None:
    with pytest.raises(ValidationError):
        ProductIn(name="  ", normalized_name="algo")
