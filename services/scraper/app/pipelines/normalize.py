"""Normalización de nombres y presentaciones.

Contrato: normalized_name = minúsculas, sin tildes, tokens canónicos,
con la presentación colapsada (ej "900 cc." -> "900cc").
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import NamedTuple


class Size(NamedTuple):
    quantity: Decimal | None
    unit: str  # canonical: kg | L | g | cc | und | pack


# El orden importa: unidades más largas antes que las cortas para que la regex
# case bien (p.ej. "lts" antes de "l", "kg" antes de "g").
#
# El prefijo "x" es opcional: docs/contracts.md define que
# "Aceite Girasol 900 cc" -> "aceite girasol 900cc", y la mayoría de las cadenas
# escriben la presentación sin la "x". Antes solo se parseaba "x 900 cc." y
# esas presentaciones quedaban enteras, partiendo productos que deberían
# colapsar en uno solo (detectado por los integration tests el 2026-10-01).
_SIZE_RE = re.compile(
    r"\s*(?:[xX]\s*)?(\d+(?:[.,]\d+)?)\s*"
    r"(cc|cm3|ml|lts|lt|litros|litro|kg|kilogramos|grs|grms|gr|g|unid|unidad|sobres|bolsas|"
    r"unidades|paquetes|paquete|pack|rollos?|und|un)\.?\s*$",
    re.IGNORECASE,
)

_CANONICAL_UNIT: dict[str, str] = {
    "cc": "cc",
    "cm3": "cc",
    "ml": "cc",
    "l": "L",
    "lt": "L",
    "lts": "L",
    "litro": "L",
    "litros": "L",
    "kg": "kg",
    "kilogramos": "kg",
    "g": "g",
    "gr": "g",
    "grs": "g",
    "grms": "g",
    "un": "und",
    "und": "und",
    "unid": "und",
    "unidad": "und",
    "unidades": "und",
    "sobres": "und",
    "bolsas": "und",
    "pack": "pack",
    "paquete": "pack",
    "paquetes": "pack",
    "rollo": "und",
    "rollos": "und",
}


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def tokenize(text: str) -> str:
    """lowercase + sin tildes + solo alfanuméricos, colapsado."""
    lowered = strip_accents(text).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", lowered).split())


def _size_from_match(match: re.Match[str]) -> Size:
    try:
        quantity = Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        quantity = None
    raw_unit = match.group(2).lower()
    # Despluralizar conservando los tokens que mapeamos explícitamente.
    if raw_unit.endswith("s") and raw_unit not in ("cc", "lts", "grs", "grms"):
        raw_unit = raw_unit[:-1]
    unit = _CANONICAL_UNIT.get(raw_unit, "und")
    return Size(quantity, unit)


def extract_size(name: str) -> Size:
    """Extrae presentación ('x 900 cc.', 'x 1 Kg.', 'x 3 Lts.') del nombre."""
    match = _SIZE_RE.search(name)
    if not match:
        return Size(None, "und")
    return _size_from_match(match)


def normalize_name(name: str) -> str:
    """Nombre canónico estable: base sin tildes + presentación colapsada."""
    match = _SIZE_RE.search(name)
    if not match:
        return tokenize(name)
    base = tokenize(name[: match.start()]).strip()
    size = _size_from_match(match)
    if size.quantity is None:
        return base
    quantity = format(size.quantity, "f")
    return f"{base} {quantity}{size.unit}"