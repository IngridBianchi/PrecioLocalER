"""Tests de app/ml: series, forecasters, anomalías y métricas."""
from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from decimal import Decimal

import pytest
from app.ml.models import (
    MODEL_HOLT_WINTERS,
    MODEL_SARIMAX,
    InsufficientDataError,
    _zscore_anomalies,
    clean_series,
    detect_anomalies,
    holt_winters,
    metrics,
    sarimax,
    walk_forward_validate,
)
from app.ml.series import build_series

START = date(2026, 1, 1)


def _series(values: list[float], *, step_days: int = 1) -> list[tuple[date, Decimal]]:
    return [
        (START + timedelta(days=i * step_days), Decimal(str(v)))
        for i, v in enumerate(values)
    ]


# ---------------------------------------------------------------------------
# build_series
# ---------------------------------------------------------------------------


def test_build_series_never_keeps_invalid_prices() -> None:
    points = _series([100, 0, -5, 110])
    series = build_series(points)
    # Días con precio no positivo (errores de scrape) no entran al modelo,
    # pero la serie debe quedar equiespaciada: el hueco se rellena hacia
    # adelante en vez de inventar un precio distinto.
    assert all(value > 0 for value in series.values)
    assert series.values[0] == 100.0
    assert series.values[-1] == 110.0
    assert len(series.days) == len(series.values)


def test_build_series_averages_duplicated_day() -> None:
    points = [
        (START, Decimal("100")),
        (START, Decimal("200")),
        (START + timedelta(days=1), Decimal("110")),
    ]
    series = build_series(points)
    assert series.values == [150.0, 110.0]


def test_build_series_carries_forward_short_gaps() -> None:
    points = _series([100, 110, 120], step_days=2)
    series = build_series(points)
    assert series.values == [100.0, 100.0, 110.0, 110.0, 120.0]
    assert series.days[1] == START + timedelta(days=1)


def test_build_series_cuts_long_gaps_instead_of_faking_trend() -> None:
    points = [
        (START, Decimal("100")),
        (START + timedelta(days=1), Decimal("110")),
        (START + timedelta(days=30), Decimal("500")),
    ]
    series = build_series(points)
    # El hueco de 28 días supera max_gap_days=3: la serie se reinicia.
    assert series.values == [500.0]
    assert series.days == [START + timedelta(days=30)]


def test_build_series_empty_input() -> None:
    assert build_series([]).is_empty
    assert build_series([(START, Decimal("0"))]).is_empty


def test_build_series_sorts_by_date() -> None:
    points = [
        (START + timedelta(days=2), Decimal("120")),
        (START, Decimal("100")),
        (START + timedelta(days=1), Decimal("110")),
    ]
    assert build_series(points).values == [100.0, 110.0, 120.0]


# ---------------------------------------------------------------------------
# Holt-Winters
# ---------------------------------------------------------------------------


def test_holt_winters_projects_trending_series_upward() -> None:
    series = build_series(_series([100, 102, 104, 106, 108, 110]))
    result = holt_winters(series, horizon=3, season_length=0)
    assert result.model == MODEL_HOLT_WINTERS
    assert len(result.points) == 3
    assert result.points[0].day == series.days[-1] + timedelta(days=1)
    assert result.points[-1].predicted_price > result.points[0].predicted_price
    assert result.points[0].predicted_price > 110


def test_holt_winters_band_is_ordered_and_non_negative() -> None:
    series = build_series(_series([100, 90, 80, 70, 60]))
    result = holt_winters(series, horizon=5, season_length=0)
    for point in result.points:
        assert point.conf_low >= 0
        assert point.conf_low <= point.predicted_price <= point.conf_high


def test_holt_winters_flat_series_has_narrow_band() -> None:
    series = build_series(_series([100] * 14))
    result = holt_winters(series, horizon=2, season_length=0)
    assert all(p.predicted_price == pytest.approx(100.0, abs=0.01) for p in result.points)
    for point in result.points:
        assert (point.conf_high - point.conf_low) < 1.0


def test_holt_winters_uses_seasonality_when_long_enough() -> None:
    # Ciclo semanal: sube y baja cada 7 días.
    weekly = [100 + (10 if i % 7 < 3 else -10) for i in range(28)]
    series = build_series(_series([float(v) for v in weekly]))
    seasonal = holt_winters(series, horizon=7, season_length=7)
    linear = holt_winters(series, horizon=7, season_length=0)
    assert seasonal.metadata["season_length"] == 7
    # El modelo estacional debeeuxctuar más que el lineal.
    seasonal_spread = max(p.predicted_price for p in seasonal.points) - min(
        p.predicted_price for p in seasonal.points
    )
    linear_spread = max(p.predicted_price for p in linear.points) - min(
        p.predicted_price for p in linear.points
    )
    assert seasonal_spread > linear_spread


def test_holt_winters_requires_minimum_points() -> None:
    series = build_series(_series([100, 101, 102]))
    with pytest.raises(InsufficientDataError):
        holt_winters(series, horizon=1, season_length=0)


# ---------------------------------------------------------------------------
# SARIMAX + fallback explícito
# ---------------------------------------------------------------------------


def test_sarimax_degrades_to_holt_winters_on_short_series() -> None:
    series = build_series(_series([100, 105, 110, 115, 120, 125]))
    result = sarimax(series, horizon=2)
    assert result.model == MODEL_HOLT_WINTERS
    assert result.degraded_reason is not None
    assert "14 puntos" in result.degraded_reason


def test_sarimax_never_claims_sarimax_without_statsmodels() -> None:
    statsmodels_available = importlib.util.find_spec("statsmodels") is not None
    series = build_series(_series([float(100 + i) for i in range(30)]))
    result = sarimax(series, horizon=3)
    if statsmodels_available:
        assert result.model == MODEL_SARIMAX
        assert result.degraded_reason is None
    else:
        assert result.model == MODEL_HOLT_WINTERS
        assert result.degraded_reason == (
            "sarimax requiere el extra .[ml] (statsmodels); se usó holtwinters"
        )


def test_sarimax_result_is_shaped_like_holt_winters() -> None:
    series = build_series(_series([float(100 + 2 * i) for i in range(30)]))
    result = sarimax(series, horizon=4)
    assert len(result.points) == 4
    for point in result.points:
        assert point.conf_low <= point.predicted_price <= point.conf_high


# ---------------------------------------------------------------------------
# Anomalías
# ---------------------------------------------------------------------------


def test_detect_anomalies_flags_obvious_outlier() -> None:
    values = [100.0] * 20
    values[10] = 5000.0
    found = detect_anomalies(values)
    assert any(a.index == 10 for a in found)


def test_detect_anomalies_clean_series_returns_empty() -> None:
    assert detect_anomalies([100.0, 101.0, 99.0, 100.5] * 3) == []


def test_detect_anomalies_needs_enough_points() -> None:
    assert detect_anomalies([1.0, 2.0, 3.0]) == []


def test_zscore_fallback_flags_outlier() -> None:
    values = [100.0] * 20
    values[3] = 400.0
    found = _zscore_anomalies(values)
    assert [a.index for a in found] == [3]
    assert found[0].reason == "robust_zscore"


def test_zscore_fallback_no_dispatch_on_constant_series() -> None:
    assert _zscore_anomalies([50.0] * 12) == []


def test_clean_series_removes_anomalies() -> None:
    values = [100.0, 101.0, 99.0, 100.5, 98.5, 101.5, 100.2, 99.8, 100.1, 99.9]
    values[4] = 9000.0
    series = build_series(_series(values))
    cleaned = clean_series(series)
    assert len(cleaned) == len(series) - 1
    assert 9000.0 not in cleaned.values


def test_clean_series_is_identity_without_anomalies() -> None:
    series = build_series(_series([float(100 + i % 3) for i in range(15)]))
    assert clean_series(series) is series


# ---------------------------------------------------------------------------
# Métricas + walk-forward
# ---------------------------------------------------------------------------


def test_metrics_perfect_prediction() -> None:
    assert metrics([100.0, 200.0], [100.0, 200.0]) == (0.0, 0.0, 0.0)


def test_metrics_known_error() -> None:
    mape, rmse, mae = metrics([100.0], [110.0])
    assert mape == pytest.approx(10.0)
    assert mae == pytest.approx(10.0)
    assert rmse == pytest.approx(10.0)


def test_metrics_ignores_zero_actuals() -> None:
    assert metrics([0.0, 100.0], [50.0, 100.0])[0] == 0.0


def test_walk_forward_returns_metrics_for_trend() -> None:
    values = [float(100 + 2 * i) for i in range(40)]
    series = build_series(_series(values))
    result = walk_forward_validate(series, forecaster=MODEL_HOLT_WINTERS, min_train=14)
    assert result.mape is not None
    assert result.n_points > 0
    # Una tendencia lineal perfecta debe predecirse muy bien.
    assert result.mape < 5.0


def test_walk_forward_rejects_unknown_forecaster() -> None:
    series = build_series(_series([float(100 + i) for i in range(30)]))
    with pytest.raises(ValueError, match="forecaster desconocido"):
        walk_forward_validate(series, forecaster="lstm")


def test_walk_forward_requires_enough_history() -> None:
    series = build_series(_series([100.0, 101.0, 102.0]))
    with pytest.raises(InsufficientDataError):
        walk_forward_validate(series, min_train=14)
