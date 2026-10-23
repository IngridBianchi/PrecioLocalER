"""Job de entrenamiento: persiste forecasts, anomalías y métricas de Python.

Por qué es un script y no un endpoint
-------------------------------------
``app/ml/api.py`` calcula el forecast bajo demanda y lo deja explícito: "el
entrenamiento diario es un job, no un endpoint público". Este script es ese job.

Qué persiste
------------
- ``forecasts``   (008_ml.sql): un punto por día futuro por (producto, cadena,
  modelo). Idempotente con ON CONFLICT.
- ``anomalies``  (008_ml.sql): los outliers que marcó el robust z-score
  (mediana + MAD); IsolationForest solo si el extra ``.[ml]`` está instalado.
- ``ml_models``  (008_ml.sql): las métricas del walk-forward con
  ``language='python'``. .NET escribe ``language='dotnet'`` en la misma tabla
  desde ``SaveMetricsAsync``; ahí está el comparativo del ADR-0006 que lee
  ``/api/forecast/{id}/comparison``.

Por qué itera por (producto, cadena) y no por producto
-----------------------------------------------------
``forecasts.chain_id`` y ``ml_models.chain_id`` son NOT NULL. La serie "de
mercado" (el promedio entre cadenas) es válida para predecir, pero no se puede
atribuir a una fila: por eso el job la ignora y solo persiste series scopeadas
a una cadena. Para ver la de mercado está el endpoint bajo demanda.

Uso
---
    python scripts/train_forecasts.py
    python scripts/train_forecasts.py --min-points 14 --horizon 7 --limit 50
    python scripts/train_forecasts.py --chain la_anonima --dry-run

Requiere ``DATABASE_URL`` en el entorno (o el default de
``scripts/smoke_e2e.py``). Es idempotente: correrlo dos veces no duplica filas.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

SCRAPER_DIR = Path(__file__).resolve().parent.parent / "services" / "scraper"
sys.path.insert(0, str(SCRAPER_DIR))

from app.config import Settings  # noqa: E402
from app.ml.models import (  # noqa: E402
    MODEL_HOLT_WINTERS,
    MODEL_SARIMAX,
    InsufficientDataError,
    clean_series,
    detect_anomalies,
    holt_winters,
    sarimax,
    walk_forward_validate,
)
from app.ml.series import build_series  # noqa: E402
from app.storage.postgres import PoolProvider, ProductRepository  # noqa: E402

DEFAULT_DATABASE_URL = "postgresql://preciolocal:preciolocal@localhost:5432/preciolocal"
LANGUAGE = "python"


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


async def train_one(
    repo: ProductRepository,
    *,
    product_id: int,
    chain_id: int,
    slug: str,
    days: int,
    horizon: int,
    season_length: int,
    contamination: float,
    model: str,
    dry_run: bool,
) -> tuple[bool, str]:
    """Entrena un par (producto, cadena). Devuelve (persistió, resumen)."""
    points = await repo.price_series_for_forecast(product_id, slug)
    if not points:
        return False, "sin price_points"

    raw = build_series(points)
    series = clean_series(raw, contamination=contamination)
    if series.is_empty:
        return False, "serie vacía tras limpiar"

    # ---- 1) Anomalías (se marcan; no se interpolan ni se eliminan) ----
    found = detect_anomalies(raw.values, contamination=contamination)
    anomaly_rows = [
        (
            datetime.combine(raw.days[a.index], datetime.min.time(), tzinfo=UTC),
            Decimal(str(a.price)),
            a.score,
        )
        for a in found
    ]

    # ---- 2) Forecast ----
    try:
        result = (
            holt_winters(series, horizon, season_length=season_length)
            if model == MODEL_HOLT_WINTERS
            else sarimax(series, horizon, season_length=season_length)
        )
    except InsufficientDataError as exc:
        return False, f"serie insuficiente: {exc}"

    # Si sarimax() degradó, YA viene con model=holtwinters y degraded_reason
    # puesto (ver app/ml/models.py). Se persiste con el nombre del modelo que
    # realmente corrió, nunca con el pedido, y se reporta la degradación.
    persisted_model = result.model
    degraded_note = f" DEGRADADO: {result.degraded_reason}" if result.degraded_reason else ""

    # ---- 3) Métricas walk-forward (comparativo ADR-0006) ----
    try:
        metrics = walk_forward_validate(
            series, forecaster=persisted_model, season_length=season_length
        )
        mape, rmse, mae = metrics.mape, metrics.rmse, metrics.mae
        n_metrics = metrics.n_points
    except (InsufficientDataError, ValueError) as exc:
        mape = rmse = mae = None
        n_metrics = 0
        metrics_note = f" sin métricas ({exc.__class__.__name__})"
    else:
        metrics_note = ""

    tag = "dry-run" if dry_run else "persistido"
    summary = (
        f"{slug}/{product_id} {days}d -> {persisted_model} [{tag}] "
        f"anomalias={len(anomaly_rows)} mape={_fmt(mape)} "
        f"rmse={_fmt(rmse)} mae={_fmt(mae)} n={n_metrics}"
        f"{metrics_note}{degraded_note}"
    )
    if dry_run:
        return True, summary

    forecast_rows = [
        (
            p.day,
            Decimal(str(p.predicted_price)),
            Decimal(str(p.conf_low)),
            Decimal(str(p.conf_high)),
        )
        for p in result.points
    ]
    if forecast_rows:
        await repo.save_forecast(
            product_id=product_id,
            chain_id=chain_id,
            model=persisted_model,
            points=forecast_rows,
            horizon_days=horizon,
        )
    await repo.save_anomalies(
        product_id=product_id, chain_id=chain_id, rows=anomaly_rows
    )
    if mape is not None:
        await repo.save_ml_metrics(
            product_id=product_id,
            chain_id=chain_id,
            model=persisted_model,
            language=LANGUAGE,
            mape=mape,
            rmse=rmse or 0.0,
            mae=mae or 0.0,
            n_points=n_metrics,
        )
    return True, summary


async def run() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Entrena y persiste los modelos ML")
    parser.add_argument("--min-points", type=int, default=14, help="mínimo de días por serie")
    parser.add_argument("--horizon", type=int, default=7, help="días a predecir")
    parser.add_argument(
        "--model",
        default=MODEL_HOLT_WINTERS,
        choices=[MODEL_SARIMAX, MODEL_HOLT_WINTERS],
        help="modelo a ejecutar (default: holtwinters, siempre disponible; "
        "sarimax requiere el extra .[ml] y degrada si falta o la serie es corta)",
    )
    parser.add_argument("--chain", default=None, help="filtra por slug de cadena")
    parser.add_argument("--limit", type=int, default=0, help="máximo de pares (0 = todos)")
    parser.add_argument("--dry-run", action="store_true", help="no escribe en la DB")
    args = parser.parse_args()

    settings = Settings()
    database_url = settings.database_url or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    provider = PoolProvider(database_url)
    await provider.start()
    repo = ProductRepository(provider)
    try:
        candidates = await repo.ml_candidates(args.min_points)
        if args.chain:
            candidates = [c for c in candidates if c[2] == args.chain]
        if args.limit:
            candidates = candidates[: args.limit]

        print(
            f"[train] {len(candidates)} par(es) con >={args.min_points} días "
            f"(modelo pedido: {args.model}, horizonte: {args.horizon}"
            f"{', dry-run' if args.dry_run else ''})"
        )
        if not candidates:
            print(
                "[train] Sin pares elegibles. Con 1 día de histórico no hay serie "
                "modelable: Holt-Winters pide 4 y SARIMAX (extra) pide 14."
            )
            return 0

        ok = 0
        for product_id, chain_id, slug, days in candidates:
            try:
                persisted, summary = await train_one(
                    repo,
                    product_id=product_id,
                    chain_id=chain_id,
                    slug=slug,
                    days=days,
                    horizon=args.horizon,
                    season_length=settings.ml_season_length,
                    contamination=settings.ml_contamination,
                    model=args.model,
                    dry_run=args.dry_run,
                )
            except Exception as exc:  # noqa: BLE001 - un producto no tumba el lote
                print(f"  ERROR {slug}/{product_id}: {exc.__class__.__name__}: {exc}")
                continue
            ok += int(persisted)
            print(f"  {summary}")
        print(f"[train] {ok}/{len(candidates)} par(es) procesados")
        return 0
    finally:
        await provider.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
