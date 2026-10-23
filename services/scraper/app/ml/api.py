"""Endpoints de ML (ADR-0006): forecast y anomalías.

Rutas bajo el prefijo `/ml`, proxyadas por nginx (ver
`web/docker/nginx.conf`). Son de lectura: el forecast se calcula bajo
demanda desde `price_points` y se cachea en Redis; no se entrena en el
request (el entrenamiento diario es un job, no un endpoint público).
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api.ratelimit import GET_LIMIT, limiter
from app.errors import APIProblem
from app.ml.models import (
    MIN_POINTS_HOLT,
    MIN_POINTS_SARIMAX,
    MODEL_HOLT_WINTERS,
    ForecastResult,
    InsufficientDataError,
    clean_series,
    holt_winters,
    sarimax,
)
from app.ml.series import PriceSeries, build_series
from app.state import AppState
from app.storage.postgres import ProductRepository

router = APIRouter()


def _state(request: Request) -> AppState:
    return request.app.state.app_state


def _repo_from(state: AppState) -> ProductRepository:
    if state.repository is None:
        raise APIProblem(503, "No configurado", "Base de datos no conectada")
    return state.repository


def _run(series: PriceSeries, model: str, horizon: int, season_length: int) -> ForecastResult:
    if model == MODEL_HOLT_WINTERS:
        return holt_winters(series, horizon, season_length=season_length)
    return sarimax(series, horizon, season_length=season_length)


@router.get("/ml/forecast/{product_id}")
@limiter.limit(GET_LIMIT)
async def forecast(
    request: Request,
    product_id: int,
    chain: str | None = Query(default=None, max_length=50),
    model: str = Query(default=MODEL_HOLT_WINTERS, pattern="^(sarimax|holtwinters)$"),
    horizon: int = Query(default=7, ge=1, le=30),
) -> dict[str, object]:
    """Predicción de precio para los próximos `horizon` días.

    El modelo de producción es Holt-Winters (`model=holtwinters`, default).
    `model=sarimax` solo corre si el extra `.[ml]` está instalado; si no, cae
    a Holt-Winters y la respuesta lo dice en `degraded_reason` en vez de
    mentir sobre el modelo usado.
    """
    repo = _repo_from(_state(request))
    points = await repo.price_series_for_forecast(product_id, chain)
    if not points:
        raise APIProblem(
            404,
            "Sin histórico",
            f"producto {product_id} no tiene price_points suficientes",
        )

    raw = build_series(points)
    if raw.is_empty:
        raise APIProblem(404, "Sin histórico", "la serie quedó vacía tras limpiar")

    series = clean_series(raw)
    try:
        result = _run(series, model, horizon, season_length=7)
    except InsufficientDataError as exc:
        min_points = (
            MIN_POINTS_HOLT if model == MODEL_HOLT_WINTERS else MIN_POINTS_SARIMAX
        )
        raise APIProblem(
            422,
            "Serie insuficiente",
            str(exc),
            n_points=len(series),
            min_points=min_points,
        ) from exc

    return {
        "product_id": product_id,
        "chain": chain,
        "requested_model": model,
        "model": result.model,
        "degraded_reason": result.degraded_reason,
        "n_points": result.n_points,
        "mape": result.mape,
        "points": [
            {
                "date": p.day.isoformat(),
                "predicted_price": p.predicted_price,
                "conf_low": p.conf_low,
                "conf_high": p.conf_high,
            }
            for p in result.points
        ],
    }


@router.get("/ml/anomalies/{product_id}")
@limiter.limit(GET_LIMIT)
async def anomalies(
    request: Request,
    product_id: int,
    chain: str | None = Query(default=None, max_length=50),
) -> dict[str, object]:
    """Puntos anómalos del histórico (IsolationForest o robust-zscore)."""
    from app.ml.models import detect_anomalies

    repo = _repo_from(_state(request))
    points = await repo.price_series_for_forecast(product_id, chain)
    if not points:
        raise APIProblem(404, "Sin histórico", f"producto {product_id} sin precios")

    series = build_series(points)
    found = detect_anomalies(series.values)
    return {
        "product_id": product_id,
        "chain": chain,
        "n_points": len(series),
        "n_anomalies": len(found),
        "detector": found[0].reason if found else None,
        "anomalies": [
            {
                "date": series.days[a.index].isoformat(),
                "price": a.price,
                "score": a.score,
                "reason": a.reason,
            }
            for a in found
        ],
    }
