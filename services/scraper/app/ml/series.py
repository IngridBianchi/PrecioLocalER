"""Preparación de series temporales de precios para los modelos de WP2.

La serie de precios es irregular: el scraper corre cuando puede, hay
promociones, errores de captura y gaps. Este módulo normaliza a una
serie diaria equiespaciada y lista para modelar.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """Un precio observado en una fecha."""

    day: date
    price: float


@dataclass(frozen=True, slots=True)
class PriceSeries:
    """Serie diaria equiespaciada, ordenada y sin nulos.

    ``values`` siempre tiene longitud ``len(days)``: los días sin
    precio se rellenan hacia adelante (last observation carried
    forward) y los días anteriores al primer precio quedan fuera.
    """

    days: list[date]
    values: list[float]

    def __len__(self) -> int:
        return len(self.values)

    @property
    def is_empty(self) -> bool:
        return not self.values

    def tail(self, n: int) -> list[float]:
        return self.values[-n:] if n < len(self.values) else list(self.values)


def _to_float(value: Decimal | float | int) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def build_series(
    points: Sequence[tuple[date, Decimal | float | int]],
    *,
    max_gap_days: int = 3,
) -> PriceSeries:
    """Construye la serie diaria a partir de observaciones sueltas.

    - Descarta precios no positivos (errores de scrape).
    - Colapsa múltiples observaciones del mismo día (promedio).
    - Rellena huecos de hasta ``max_gap_days`` con el último precio
      conocido; huecos mayores se dejan fuera de la serie en vez de
      inventar un precio plano largo (distorsionaría el modelo).
    - Ordena por fecha.
    """
    if not points:
        return PriceSeries(days=[], values=[])

    by_day: dict[date, list[float]] = {}
    for day, raw in points:
        value = _to_float(raw)
        if value <= 0:
            continue
        by_day.setdefault(day, []).append(value)

    if not by_day:
        return PriceSeries(days=[], values=[])

    daily = {day: sum(values) / len(values) for day, values in by_day.items()}
    ordered_days = sorted(daily)

    days: list[date] = []
    values: list[float] = []
    previous_day: date | None = None
    previous_value: float | None = None

    for day in ordered_days:
        value = daily[day]
        if previous_day is not None and previous_value is not None:
            gap = (day - previous_day).days - 1
            if gap > max_gap_days:
                # Hueco demasiado grande: cortamos la serie y arrancamos
                # de nuevo en este día (no inventamos tendencia).
                days = []
                values = []
            else:
                for offset in range(1, gap + 1):
                    days.append(previous_day + timedelta(days=offset))
                    values.append(previous_value)
        days.append(day)
        values.append(value)
        previous_day = day
        previous_value = value

    return PriceSeries(days=days, values=values)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: list[float]) -> float:
    """Desvío estándar poblacional (0 para series de un solo punto)."""
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    variance = sum((v - avg) ** 2 for v in values) / len(values)
    return variance**0.5
