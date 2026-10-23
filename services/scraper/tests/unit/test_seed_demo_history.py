"""Tests de la lógica pura de `scripts/seed_demo_history.py`.

Lo que no necesita base: las guardas de entorno y la dinámica del precio
simulado. Estas funciones son las que deciden si el script puede correr y qué
histórico sintético genera, así que se prueban sin Postgres para que el fallo
sea obvio.

Lo que sí escribe (productos, price_points, `--purge`) está en
`tests/integration/test_seed_demo_history.py`.
"""
from __future__ import annotations

import random
from decimal import Decimal

import pytest
from app.pipelines.normalize import normalize_name
from scripts.seed_demo_history import (
    DEMO_PREFIX,
    MONTHLY_INFLATION,
    PROMO_DEPTH,
    PROMO_EVERY_DAYS,
    _guard,
    _price_for_day,
)

# ---------------------------------------------------------------------------
# Guardas duras: lo que impide que el script toque la base real
# ---------------------------------------------------------------------------


def test_guard_aborts_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarda 3: datos sintéticos no entran a producción ni a propósito."""
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    with pytest.raises(SystemExit, match="production"):
        _guard("postgresql://u:p@127.0.0.1:5432/preciolocal", allow_remote=False)


def test_guard_aborts_on_remote_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarda 3: sin --allow-remote, una base que no sea local se rechaza."""
    monkeypatch.setenv("APP_ENVIRONMENT", "development")
    with pytest.raises(SystemExit, match="no es local"):
        _guard("postgresql://u:p@db.produccion.example.com:5432/preciolocal", allow_remote=False)


def test_guard_allows_remote_when_explicitly_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "development")
    _guard("postgresql://u:p@db.example.com:5432/preciolocal", allow_remote=True)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_guard_allows_local_hosts(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "development")
    _guard(f"postgresql://u:p@{host}:5432/preciolocal", allow_remote=False)


def test_allow_remote_is_not_a_key_to_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--allow-remote` es para staging, no una puerta a producción."""
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    with pytest.raises(SystemExit, match="production"):
        _guard("postgresql://u:p@db.example.com:5432/preciolocal", allow_remote=True)


def test_guard_reads_the_host_before_the_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """El host se parsea del DSN; un puerto y un path no deben confundirlo."""
    monkeypatch.setenv("APP_ENVIRONMENT", "development")
    _guard("postgresql://u:p@localhost:6543/otra/base", allow_remote=False)


# ---------------------------------------------------------------------------
# La dinámica simulada: tiene que ser aprendible, no ruido blanco
# ---------------------------------------------------------------------------


def test_price_trends_up_over_the_month() -> None:
    base = Decimal("5000")
    first = _price_for_day(base, 0, random.Random(1))
    last = _price_for_day(base, 60, random.Random(1))
    assert last > first, "con inflación mensual el final tiene que estar arriba"


def test_price_applies_the_configured_monthly_inflation() -> None:
    """En un mes exacto el factor es (1 + MONTHLY_INFLATION)."""
    base = Decimal("1000")
    flat = _price_for_day(base, 0, random.Random(99))
    drifted = _price_for_day(base, 30, random.Random(99))
    expected = flat * Decimal(str(1 + MONTHLY_INFLATION))
    assert abs(drifted - expected) <= base * Decimal("0.0001")


def test_weekend_is_more_expensive_than_weekday() -> None:
    """La semana base arranca en un lunes, así que el finde es day_index 5."""
    base = Decimal("5000")
    monday = _price_for_day(base, 0, random.Random(3))
    saturday = _price_for_day(base, 5, random.Random(3))
    assert saturday > monday


def test_promo_drops_the_price_on_its_day() -> None:
    base = Decimal("5000")
    prev = _price_for_day(base, PROMO_EVERY_DAYS - 1, random.Random(5))
    promo = _price_for_day(base, PROMO_EVERY_DAYS, random.Random(5))
    assert promo / prev < 1 - PROMO_DEPTH * Decimal("0.5")


def test_no_promo_on_the_first_day() -> None:
    """Día 0 queda fuera del descuento: no hay precio anterior con el cual comparar."""
    assert _price_for_day(Decimal("5000"), 0, random.Random(11)) > Decimal("1000")


def test_price_is_always_positive_and_rounded_to_cents() -> None:
    rng = random.Random(13)
    for day in range(90):
        price = _price_for_day(Decimal("500"), day, rng)
        assert price > 0
        assert price == price.quantize(Decimal("0.01"))


def test_simulation_is_deterministic_for_a_given_seed() -> None:
    """El mismo seed tiene que dar la misma serie: si no, la demo no es repetible."""
    a = [_price_for_day(Decimal("5000"), d, random.Random(1000)) for d in range(10)]
    b = [_price_for_day(Decimal("5000"), d, random.Random(1000)) for d in range(10)]
    assert a == b


# ---------------------------------------------------------------------------
# El prefijo DEMO-: que ningún dato sintético se confunda con uno real
# ---------------------------------------------------------------------------


def test_normalized_demo_name_is_prefixed() -> None:
    """Contrato que leen el job de entrenamiento y cualquier export."""
    assert normalize_name(f"{DEMO_PREFIX}Aceite Girasol 900cc").startswith("demo")


def test_demo_prefix_is_not_a_valid_real_product_prefix() -> None:
    """Un producto real nunca empieza con DEMO-: por eso el purge es seguro."""
    assert not normalize_name("Aceite Girasol 900cc").startswith("demo")
