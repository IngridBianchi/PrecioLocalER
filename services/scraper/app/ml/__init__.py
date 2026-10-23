"""Modelos de ML de WP2 (ADR-0006): forecasting + calidad de datos.

Importa desde acá: `app.ml.models` (algoritmos), `app.ml.series`
(normalización de la serie) y `app.ml.api` (endpoints `/ml/*`).
"""
from app.ml.models import (
    MODEL_HOLT_WINTERS,
    MODEL_SARIMAX,
    Anomaly,
    ForecastPoint,
    ForecastResult,
    InsufficientDataError,
    clean_series,
    detect_anomalies,
    holt_winters,
    metrics,
    sarimax,
    walk_forward_validate,
)
from app.ml.series import PriceSeries, SeriesPoint, build_series

__all__ = [
    "MODEL_HOLT_WINTERS",
    "MODEL_SARIMAX",
    "Anomaly",
    "ForecastPoint",
    "ForecastResult",
    "InsufficientDataError",
    "PriceSeries",
    "SeriesPoint",
    "build_series",
    "clean_series",
    "detect_anomalies",
    "holt_winters",
    "metrics",
    "sarimax",
    "walk_forward_validate",
]
