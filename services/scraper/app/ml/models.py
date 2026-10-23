"""Modelos de forecasting de WP2 (ADR-0006).

Modelos de producción, sin dependencias pesadas:

- **Holt-Winters** (implementación pura en Python) para el forecast.
- **Robust z-score** (mediana + MAD) para marcar anomalías.

``statsmodels`` y ``scikit-learn`` son el extra opcional ``.[ml]``: habilitan
SARIMAX e IsolationForest para experimentación local, pero **no** se instalan
en la imagen base. Así el servicio no carga ~200 MB de dependencias en el free
tier (ADR-0001/0007) y los tests corren en cualquier entorno.

Si alguien pide explícitamente ``model=sarimax`` sin el extra, el resultado
degrada a Holt-Winters con un ``degraded_reason`` visible; nunca se reporta
como SARIMAX algo que no corrió.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.ml.series import PriceSeries, mean, stdev

MODEL_SARIMAX = "sarimax"
MODEL_HOLT_WINTERS = "holtwinters"


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """Predicción para un día futuro."""

    day: date
    predicted_price: float
    conf_low: float
    conf_high: float


@dataclass(frozen=True, slots=True)
class ForecastResult:
    """Salida de un forecaster + métricas de la validación walk-forward."""

    model: str
    points: list[ForecastPoint]
    mape: float | None = None
    rmse: float | None = None
    mae: float | None = None
    n_points: int = 0
    degraded_reason: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class InsufficientDataError(ValueError):
    """La serie no tiene puntos suficientes para modelar."""


# ---------------------------------------------------------------------------
# Holt-Winters (doble exponencial con estacionalidad aditiva opcional)
# ---------------------------------------------------------------------------

MIN_POINTS_HOLT = 4


def _residual_sigma(actual: list[float], fitted: list[float]) -> float:
    """Sigma de los residuos del ajuste in-sample.

    Se usa para la banda de confianza cuando el modelo no la provee
    (Holt-Winters puro). Con pocos residuos cae al desvío de la serie,
    que para una serie plana da una banda casi de ancho cero.
    """
    residuals = [
        a - f for a, f in zip(actual, fitted, strict=False) if f is not None
    ]
    sigma = stdev(residuals)
    if sigma > 0:
        return sigma
    return max(stdev(actual), 0.0)


def holt_winters(
    series: PriceSeries,
    horizon: int = 7,
    *,
    season_length: int = 7,
    alpha: float = 0.3,
    beta: float = 0.1,
    gamma: float = 0.2,
) -> ForecastResult:
    """Holt-Winters aditivo.

    Con ``season_length <= 1`` degrada a Holt lineal (nivel + tendencia),
    que es el comportamiento correcto cuando la serie no tiene ciclo.
    """
    values = series.values
    if len(values) < MIN_POINTS_HOLT:
        raise InsufficientDataError(
            f"Holt-Winters requiere >= {MIN_POINTS_HOLT} puntos, hay {len(values)}"
        )

    use_season = season_length > 1 and len(values) >= 2 * season_length
    level = values[0]
    trend = values[1] - values[0] if len(values) > 1 else 0.0
    season: list[float] = []

    if use_season:
        # Inicialización estacional: promedio de cada posición del ciclo.
        buckets: list[list[float]] = [[] for _ in range(season_length)]
        for index, value in enumerate(values):
            buckets[index % season_length].append(value)
        season = [mean(bucket) for bucket in buckets]
        # Centramos la estacionalidad (suma ~0) para no duplicar tendencia.
        season_mean = mean(season)
        season = [s - season_mean for s in season]

    fitted: list[float] = []
    for index, value in enumerate(values):
        seasonal = season[index % season_length] if use_season else 0.0

        prediction = level + trend + seasonal
        fitted.append(prediction)

        last_level = level
        level = alpha * (value - seasonal) + (1 - alpha) * (level + trend)
        trend = beta * (level - last_level) + (1 - beta) * trend

        if use_season:
            season[index % season_length] = (
                gamma * (value - level) + (1 - gamma) * seasonal
            )

    last_day = series.days[-1]
    last_index = len(values) - 1
    residual_sigma = _residual_sigma(values, fitted)
    width = 1.96 * residual_sigma

    points: list[ForecastPoint] = []
    for step in range(1, horizon + 1):
        seasonal = season[(last_index + step) % season_length] if use_season else 0.0
        predicted = max(0.0, level + step * trend + seasonal)
        points.append(
            ForecastPoint(
                day=last_day + timedelta(days=step),
                predicted_price=round(predicted, 2),
                conf_low=round(max(0.0, predicted - width), 2),
                conf_high=round(predicted + width, 2),
            )
        )

    return ForecastResult(
        model=MODEL_HOLT_WINTERS,
        points=points,
        n_points=len(values),
        metadata={
            "season_length": season_length if use_season else 0,
            "level": round(level, 4),
            "trend": round(trend, 4),
        },
    )


# ---------------------------------------------------------------------------
# SARIMAX (statsmodels) con fallback explícito a Holt-Winters
# ---------------------------------------------------------------------------

MIN_POINTS_SARIMAX = 14


def sarimax(
    series: PriceSeries,
    horizon: int = 7,
    *,
    season_length: int = 7,
) -> ForecastResult:
    """SARIMAX(1,1,1)x(1,1,1,7) vía statsmodels (extra ``.[ml]``).

    No es el modelo de producción. Si statsmodels no está instalado, o la
    serie es demasiado corta, devuelve Holt-Winters con ``degraded_reason``
    puesto: un pedido explícito de SARIMAX nunca debe reportarse como cumplido
    si en realidad predijo otro modelo.
    """
    if len(series) < MIN_POINTS_SARIMAX:
        result = holt_winters(series, horizon, season_length=season_length)
        return ForecastResult(
            model=MODEL_HOLT_WINTERS,
            points=result.points,
            n_points=result.n_points,
            degraded_reason=(
                f"sarimax requiere >= {MIN_POINTS_SARIMAX} puntos, "
                f"hay {len(series)}; fallback a holtwinters"
            ),
            metadata=result.metadata,
        )

    try:
        import numpy as np
        from statsmodels.tsa.statespace.sarimax import SARIMAX
    except ImportError:
        result = holt_winters(series, horizon, season_length=season_length)
        return ForecastResult(
            model=MODEL_HOLT_WINTERS,
            points=result.points,
            n_points=result.n_points,
            degraded_reason=(
                "sarimax requiere el extra .[ml] (statsmodels); "
                "se usó holtwinters"
            ),
            metadata=result.metadata,
        )

    order = (1, 1, 1)
    seasonal_order = (1, 1, 1, season_length) if season_length > 1 else (0, 0, 0, 0)
    target = np.asarray(series.values, dtype=float)

    try:
        fitted = SARIMAX(
            target,
            order=order,
            seasonal_order=seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit(disp=False)
        prediction = fitted.get_forecast(steps=horizon)
        mean_forecast = np.asarray(prediction.predicted_mean, dtype=float)
        conf = np.asarray(prediction.conf_int(), dtype=float)
    except Exception as exc:  # noqa: BLE001 - convergencia: fallback, no 500
        result = holt_winters(series, horizon, season_length=season_length)
        return ForecastResult(
            model=MODEL_HOLT_WINTERS,
            points=result.points,
            n_points=result.n_points,
            degraded_reason=f"sarimax no convergió ({exc.__class__.__name__}); fallback",
            metadata=result.metadata,
        )

    last_day = series.days[-1]
    points: list[ForecastPoint] = []
    for step in range(horizon):
        predicted = max(0.0, float(mean_forecast[step]))
        low = max(0.0, float(conf[step][0]))
        high = max(0.0, float(conf[step][1]))
        points.append(
            ForecastPoint(
                day=last_day + timedelta(days=step + 1),
                predicted_price=round(predicted, 2),
                conf_low=round(low, 2),
                conf_high=round(high, 2),
            )
        )

    return ForecastResult(
        model=MODEL_SARIMAX,
        points=points,
        n_points=len(series),
        metadata={"order": order, "seasonal_order": seasonal_order},
    )


# ---------------------------------------------------------------------------
# IsolationForest (scikit-learn) con fallback de robust-zscore
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Anomaly:
    """Punto marcado como anómalo."""

    index: int
    price: float
    score: float
    reason: str


def detect_anomalies(
    values: list[float],
    *,
    contamination: float = 0.05,
) -> list[Anomaly]:
    """Detecta outliers para limpiar el histórico antes de entrenar.

    Implementación de producción: robust z-score (mediana + MAD). Si el extra
    ``.[ml]`` instaló scikit-learn, usa IsolationForest. En ambos casos solo
    marca los puntos; no elimina ni interpola precios.
    """
    if len(values) < 8:
        return []

    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        return _zscore_anomalies(values)

    import numpy as np

    matrix = np.asarray(values, dtype=float).reshape(-1, 1)
    detector = IsolationForest(
        contamination=contamination,
        random_state=42,
        n_estimators=100,
    )
    raw_scores = detector.fit_predict(matrix)
    scores = detector.score_samples(matrix)

    anomalies: list[Anomaly] = []
    for index, prediction in enumerate(raw_scores):
        if prediction == -1:
            anomalies.append(
                Anomaly(
                    index=index,
                    price=values[index],
                    score=round(float(scores[index]), 6),
                    reason="isolation_forest",
                )
            )
    return anomalies


def _zscore_anomalies(values: list[float]) -> list[Anomaly]:
    """Fallback robusto: mediana + MAD, threshold 3.5 (regla 1.5*IQR/0.6745)."""
    ordered = sorted(values)
    count = len(ordered)
    median = (
        ordered[count // 2]
        if count % 2 == 1
        else (ordered[count // 2 - 1] + ordered[count // 2]) / 2
    )
    deviations = sorted(abs(v - median) for v in values)
    mad = (
        deviations[count // 2]
        if count % 2 == 1
        else (deviations[count // 2 - 1] + deviations[count // 2]) / 2
    )

    if mad > 0:
        center, sigma = median, 1.4826 * mad
    else:
        # MAD == 0 ocurre cuando más de la mitad de la serie es idéntica
        # (típico: precio constante + un scrape roto). En ese caso el MAD
        # no discriminate, así que caemos al desvío estándar. Si tampoco
        # hay dispersión, la serie es plana y no hay anomalías.
        sigma = stdev(values)
        if sigma == 0:
            return []
        center = mean(values)

    anomalies: list[Anomaly] = []
    for index, value in enumerate(values):
        score = abs(value - center) / sigma
        if score > 3.5:
            anomalies.append(
                Anomaly(
                    index=index,
                    price=value,
                    score=round(score, 6),
                    reason="robust_zscore",
                )
            )
    return anomalies


def clean_series(series: PriceSeries, *, contamination: float = 0.05) -> PriceSeries:
    """Quita (no interpola) los puntos marcados como anómalos.

    ADR-0006: IsolationForest es feature de calidad de datos; los
    forecasters entrenan sin los outliers. Si se limpian todos los
    puntos, se devuelve la serie original (no dejaríamos nada).
    """
    anomalies = detect_anomalies(series.values, contamination=contamination)
    if not anomalies:
        return series
    bad = {anomaly.index for anomaly in anomalies}
    if len(bad) >= len(series):
        return series
    return PriceSeries(
        days=[day for index, day in enumerate(series.days) if index not in bad],
        values=[value for index, value in enumerate(series.values) if index not in bad],
    )


# ---------------------------------------------------------------------------
# Métricas + walk-forward (para el comparativo del ADR-0006, WP5)
# ---------------------------------------------------------------------------


def metrics(actual: list[float], predicted: list[float]) -> tuple[float, float, float]:
    """(MAPE, RMSE, MAE) en porcentaje, sin dividir por cero."""
    pairs = [
        (a, p)
        for a, p in zip(actual, predicted, strict=False)
        if a > 0
    ]
    if not pairs:
        return 0.0, 0.0, 0.0
    errors = [(a - p) / a for a, p in pairs]
    mape = mean([abs(e) for e in errors]) * 100
    rmse = math.sqrt(mean([((a - p) ** 2) for a, p in pairs]))
    mae = mean([abs(a - p) for a, p in pairs])
    return round(mape, 4), round(rmse, 4), round(mae, 4)


def walk_forward_validate(
    series: PriceSeries,
    *,
    forecaster: str = MODEL_SARIMAX,
    min_train: int = 14,
    horizon: int = 1,
    season_length: int = 7,
    max_folds: int = 10,
) -> ForecastResult:
    """Backtesting walk-forward: error real sobre folds cronológicos.

    Para cada fold: entrena con todo lo anterior, predice ``horizon``
    días y compara contra la realidad. Es la validación honesta para
    retail (no se puede hacer split aleatorio: fuga de futuro).
    """
    values = series.values
    if forecaster not in (MODEL_SARIMAX, MODEL_HOLT_WINTERS):
        raise ValueError(f"forecaster desconocido: {forecaster}")

    minimum = max(min_train, MIN_POINTS_HOLT)
    if len(values) <= minimum:
        raise InsufficientDataError(
            f"walk-forward requiere > {minimum} puntos, hay {len(values)}"
        )

    actual: list[float] = []
    predicted: list[float] = []

    fold_starts = range(minimum, len(values) - horizon + 1)
    if len(fold_starts) > max_folds:
        cutoff = len(values) - horizon - max_folds + 1
        fold_starts = range(cutoff, len(values) - horizon + 1)

    for start in fold_starts:
        train = PriceSeries(days=series.days[:start], values=values[:start])
        try:
            if forecaster == MODEL_SARIMAX:
                result = sarimax(train, horizon, season_length=season_length)
            else:
                result = holt_winters(train, horizon, season_length=season_length)
        except InsufficientDataError:
            continue
        if not result.points:
            continue
        truth = values[start : start + horizon]
        for offset, observed in enumerate(truth):
            if offset < len(result.points):
                actual.append(observed)
                predicted.append(result.points[offset].predicted_price)

    mape, rmse, mae = metrics(actual, predicted)
    return ForecastResult(
        model=forecaster,
        points=[],
        mape=mape,
        rmse=rmse,
        mae=mae,
        n_points=len(actual),
    )


def sigma_of(values: list[float]) -> float:
    return stdev(values)
