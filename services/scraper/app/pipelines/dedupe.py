"""Dedupe por hash del price_point (ver 001_init.sql: sha256(producto|precio|captura))."""
import hashlib


def compute_price_hash(chain_id: int, sku_id: int, price: str, captured_at: str) -> str:
    """Hash estable que hace idempotente la captura.

    Incluye cadena + SKU para que dos SKU distintos de la misma cadena
    (p. ej. Coto 531487/531488) no colisionen ni se pise el histórico.

    La columna price_points.hash es UNIQUE -> ON CONFLICT (hash) DO NOTHING
    garantiza que re-scrapear el mismo día no duplique filas.
    """
    key = f"{chain_id}|{sku_id}|{price}|{captured_at}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()