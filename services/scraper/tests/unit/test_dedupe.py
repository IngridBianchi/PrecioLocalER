"""Tests del hash de dedupe (idempotencia del scraper)."""
from app.pipelines.dedupe import compute_price_hash


def test_hash_is_stable() -> None:
    a = compute_price_hash(1, 10, "5350", "2026-09-23T10:00:00Z")
    b = compute_price_hash(1, 10, "5350", "2026-09-23T10:00:00Z")
    assert a == b


def test_hash_changes_with_price() -> None:
    a = compute_price_hash(1, 10, "5350", "2026-09-23T10:00:00Z")
    b = compute_price_hash(1, 10, "5400", "2026-09-23T10:00:00Z")
    assert a != b


def test_hash_changes_with_date() -> None:
    a = compute_price_hash(1, 10, "5350", "2026-09-23T10:00:00Z")
    b = compute_price_hash(1, 10, "5350", "2026-09-24T10:00:00Z")
    assert a != b


def test_hash_changes_with_chain() -> None:
    a = compute_price_hash(1, 10, "5350", "2026-09-23T10:00:00Z")
    b = compute_price_hash(2, 10, "5350", "2026-09-23T10:00:00Z")
    assert a != b


def test_hash_changes_with_sku() -> None:
    """Regresión incidente 2026-09-24: dos SKU de la misma cadena no rozan."""
    a = compute_price_hash(4, 531487, "2129", "2026-09-24T10:00:00Z")
    b = compute_price_hash(4, 531488, "2129", "2026-09-24T10:00:00Z")
    assert a != b


def test_hash_is_sha256_hex() -> None:
    value = compute_price_hash(1, 10, "100", "2026-09-23T10:00:00Z")
    assert len(value) == 64
    int(value, 16)