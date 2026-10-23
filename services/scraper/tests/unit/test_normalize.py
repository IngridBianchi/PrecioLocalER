"""Tests unitarios de normalización de nombres y presentaciones."""
from decimal import Decimal

from app.pipelines.normalize import extract_size, normalize_name, strip_accents, tokenize


def test_strip_accents_removes_tildes() -> None:
    assert strip_accents("Aceite de Girasol La Anónima") == "Aceite de Girasol La Anonima"


def test_tokenize_lowercases_and_collapses() -> None:
    assert tokenize("  Aceite   de  Girasol 900cc  ") == "aceite de girasol 900cc"


def test_normalize_name_with_cc_suffix() -> None:
    assert (
        normalize_name("Aceite de Girasol La Anónima x 900 cc.")
        == "aceite de girasol la anonima 900cc"
    )


def test_normalize_name_with_kg_variant() -> None:
    assert normalize_name("Harina de Trigo 0000 La Anonima x 1 Kg.") == (
        "harina de trigo 0000 la anonima 1kg"
    )


def test_normalize_name_without_space_before_kg() -> None:
    assert normalize_name("Harina 000 Victoria x 1Kg.") == "harina 000 victoria 1kg"


def test_extract_size_cc() -> None:
    size = extract_size("Rocío Vegetal de Aceite Girasol Natura x 126 cc.")
    assert size.quantity == Decimal("126")
    assert size.unit == "cc"


def test_extract_size_liters_plural() -> None:
    size = extract_size("Aceite de Girasol Natura x 3 Lts.")
    assert size.quantity == Decimal("3")
    assert size.unit == "L"


def test_extract_size_grams() -> None:
    size = extract_size("Premium Chipa sin Tacc Pureza x 250 g.")
    assert size.quantity == Decimal("250")
    assert size.unit == "g"


def test_no_size_means_undefined_unit() -> None:
    size = extract_size("Leche entera larga vida")
    assert size.quantity is None
    assert size.unit == "und"


def test_decimal_quantity_with_comma() -> None:
    size = extract_size("Yerba x 0,5 kg.")
    assert size.quantity == Decimal("0.5")
    assert size.unit == "kg"


# --- Presentaciones sin la "x" (contrato de docs/contracts.md) ---------------
# La mayoría de las cadenas escriben "Aceite Girasol 900 cc" sin multiplicador.
# Antes la regex exigía la "x" y esas presentaciones no se parseaban, dejando
# el nombre entero y partiendo en dos productos que son el mismo.


def test_normalize_name_without_x_prefix_matches_contract() -> None:
    assert normalize_name("Aceite Girasol 900 cc") == "aceite girasol 900cc"


def test_x_prefixed_and_bare_presentation_collapse_to_same_key() -> None:
    """El dedupe depende de esto: son el mismo producto."""
    assert normalize_name("Aceite Girasol 900 cc") == normalize_name("Aceite Girasol x 900 cc.")


def test_extract_size_without_x_prefix() -> None:
    size = extract_size("Aceite Girasol 900 cc")
    assert size.quantity == Decimal("900")
    assert size.unit == "cc"


def test_extract_size_without_x_prefix_kg() -> None:
    size = extract_size("Harina de Trigo 000 1 kg")
    assert size.quantity == Decimal("1")
    assert size.unit == "kg"


def test_extract_size_without_x_prefix_decimal_comma() -> None:
    size = extract_size("Yerba Mate 0,5 kg")
    assert size.quantity == Decimal("0.5")
    assert size.unit == "kg"


def test_extract_size_bare_number_in_middle_is_not_a_size() -> None:
    """Un número suelto que no precede a una unidad no es presentación."""
    size = extract_size("Harina 0000 La Anonima")
    assert size.quantity is None
    assert size.unit == "und"


def test_normalize_name_pack_multiplier_with_x() -> None:
    assert normalize_name("Papel Higienico 6 x 4 rollos") == "papel higienico 6 4und"