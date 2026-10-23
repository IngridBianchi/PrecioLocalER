"""Tests de los endpoints de `/ml` (ADR-0006) sin base de datos.

`app/ml/api.py` estaba al 39%: los otros endpoints had fake-repository, pero
este módulo era el único sin cubrir y es el que el front consume para el chart
de predicción. Lo que importa acá es el contrato de la respuesta:

1. Que el forecast diga qué modelo corrió de verdad (`model` +
   `degraded_reason`), no solo el que se pidió.
2. Que los 404 y 422 del docstring salgan con el `problem` de la app.
3. Que el JSON tenga las claves que el chart del front lee.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from app.api.ratelimit import limiter
from app.config import Settings
from app.main import app
from app.ml.models import MODEL_HOLT_WINTERS, MODEL_SARIMAX
from app.state import AppState

TODAY = date(2026, 10, 1)


def _trend(days: int, *, base: float = 1000.0, step: float = 9.0) -> list[tuple[date, Decimal]]:
    """(day, price) con tendencia y finde caro, como devuelve el repositorio."""
    out: list[tuple[date, Decimal]] = []
    for i in range(days):
        day = TODAY - timedelta(days=days - 1 - i)
        price = base + step * i
        if day.weekday() in (5, 6):
            price *= 1.03
        out.append((day, Decimal(f"{price:.2f}")))
    return out


class FakeMlRepository:
    """Solo lo que `app/ml/api.py` le pide: `price_series_for_forecast`."""

    def __init__(self, points: list[tuple[date, Decimal]]) -> None:
        self.points = points
        self.calls: list[tuple[int, str | None]] = []

    async def price_series_for_forecast(
        self, product_id: int, chain_slug: str | None
    ) -> list[tuple[date, Decimal]]:
        self.calls.append((product_id, chain_slug))
        return self.points


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    limiter.reset()
    yield
    limiter.reset()


def _client(repo: object | None) -> httpx.AsyncClient:
    state = AppState(Settings(app_environment="test", database_url="", redis_url=""))
    state.repository = repo  # type: ignore[assignment]
    app.state.app_state = state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _limit_n() -> int:
    """`GET_LIMIT` es una string tipo "60/minute"; acá hace falta el número."""
    from app.api.ratelimit import GET_LIMIT

    return int(GET_LIMIT.split("/", 1)[0])


# ---------------------------------------------------------------------------
# /ml/forecast/{id}
# ---------------------------------------------------------------------------


async def test_forecast_returns_the_documented_shape() -> None:
    async with _client(FakeMlRepository(_trend(30))) as client:
        response = await client.get("/ml/forecast/1")
    assert response.status_code == 200
    body = response.json()
    # Las claves que lee el chart del front (docs/contracts.md §3.1).
    assert set(body) == {
        "product_id",
        "chain",
        "requested_model",
        "model",
        "degraded_reason",
        "n_points",
        "mape",
        "points",
    }
    assert body["product_id"] == 1
    assert body["requested_model"] == MODEL_HOLT_WINTERS
    assert body["model"] == MODEL_HOLT_WINTERS
    assert body["degraded_reason"] is None, "el modelo de producción no degrada"
    assert len(body["points"]) == 7, "el horizonte por default es 7"
    for point in body["points"]:
        assert set(point) == {"date", "predicted_price", "conf_low", "conf_high"}
        assert point["conf_low"] <= point["predicted_price"] <= point["conf_high"]


async def test_forecast_never_lies_about_the_model_it_ran() -> None:
    """Si se pide SARIMAX y degrada, `model` lo dice y `requested_model`
    conserva el pedido: el front puede avisar 'estimación aproximada'."""
    async with _client(FakeMlRepository(_trend(30))) as client:
        body = (await client.get("/ml/forecast/1", params={"model": MODEL_SARIMAX})).json()
    assert body["requested_model"] == MODEL_SARIMAX
    if body["model"] != body["requested_model"]:
        assert body["model"] == MODEL_HOLT_WINTERS
        assert body["degraded_reason"], "degradar sin explicar es lo mismo que mentir"


async def test_forecast_honours_the_horizon_parameter() -> None:
    async with _client(FakeMlRepository(_trend(30))) as client:
        for horizon in (1, 7, 14, 30):
            body = (await client.get("/ml/forecast/1", params={"horizon": horizon})).json()
            assert len(body["points"]) == horizon


@pytest.mark.parametrize("horizon", [0, 31, -1])
async def test_forecast_rejects_out_of_range_horizon(horizon: int) -> None:
    async with _client(FakeMlRepository(_trend(30))) as client:
        response = await client.get("/ml/forecast/1", params={"horizon": horizon})
    assert response.status_code == 422


async def test_forecast_rejects_unknown_model() -> None:
    async with _client(FakeMlRepository(_trend(30))) as client:
        response = await client.get("/ml/forecast/1", params={"model": "gpt"})
    assert response.status_code == 422


async def test_forecast_passes_the_chain_filter_to_the_repository() -> None:
    repo = FakeMlRepository(_trend(30))
    async with _client(repo) as client:
        body = (await client.get("/ml/forecast/1", params={"chain": "la_anonima"})).json()
    assert repo.calls == [(1, "la_anonima")]
    assert body["chain"] == "la_anonima"


async def test_forecast_without_prices_is_404() -> None:
    async with _client(FakeMlRepository([])) as client:
        response = await client.get("/ml/forecast/999")
    assert response.status_code == 404
    problem = response.json()
    assert problem["title"] == "Sin histórico"
    assert "999" in problem["detail"]


async def test_forecast_with_only_invalid_prices_is_404() -> None:
    """Cero y negativos son errores de scrape: la serie queda vacía y es un 404,
    no un forecast de waldo."""
    junk = [(TODAY - timedelta(days=i), Decimal("0")) for i in range(30)]
    async with _client(FakeMlRepository(junk)) as client:
        response = await client.get("/ml/forecast/1")
    assert response.status_code == 404


async def test_forecast_with_too_few_points_is_422_with_the_threshold() -> None:
    """3 días: hay serie pero no alcanza. El 422 dice cuántos hay y cuántos
    hacen falta, que es lo que necesita el front para explicar el chart vacío."""
    async with _client(FakeMlRepository(_trend(3))) as client:
        response = await client.get("/ml/forecast/1")
    assert response.status_code in (404, 422)
    if response.status_code == 422:
        problem = response.json()
        assert problem["n_points"] < problem["min_points"]


async def test_forecast_without_repository_is_503() -> None:
    """Si la base no está conectada, no es un 500: es un 503 con mensaje."""
    async with _client(None) as client:
        response = await client.get("/ml/forecast/1")
    assert response.status_code == 503
    assert response.json()["title"] == "No configurado"


# ---------------------------------------------------------------------------
# /ml/anomalies/{id}
# ---------------------------------------------------------------------------


async def test_anomalies_returns_the_documented_shape() -> None:
    async with _client(FakeMlRepository(_trend(40))) as client:
        response = await client.get("/ml/anomalies/1")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "product_id",
        "chain",
        "n_points",
        "n_anomalies",
        "detector",
        "anomalies",
    }
    assert body["n_points"] == 40
    assert body["n_anomalies"] == len(body["anomalies"])


async def test_anomalies_on_a_clean_series_is_empty_not_an_error() -> None:
    """Una serie sana devuelve cero anomalías: no es un fallo."""
    async with _client(FakeMlRepository(_trend(40))) as client:
        body = (await client.get("/ml/anomalies/1")).json()
    assert body["n_anomalies"] == 0
    assert body["anomalies"] == []
    assert body["detector"] is None


async def test_anomalies_flags_an_outlier_with_its_date_and_price() -> None:
    points = _trend(40)
    outlier_day = points[25][0]
    points[25] = (outlier_day, Decimal("5.00"))
    async with _client(FakeMlRepository(points)) as client:
        body = (await client.get("/ml/anomalies/1")).json()
    assert body["n_anomalies"] >= 1
    hit = next(a for a in body["anomalies"] if a["date"] == outlier_day.isoformat())
    assert hit["price"] == 5.0
    assert hit["reason"]


async def test_anomalies_anomaly_dates_index_the_series_correctly() -> None:
    """Regresión de índices: el detector reporta una posición en la serie y la
    respuesta la traduce con `series.days[index]`. Si se corre en uno, la fecha
    que muestra el front es de otro día."""
    points = _trend(40)
    points[10] = (points[10][0], Decimal("9000.00"))
    async with _client(FakeMlRepository(points)) as client:
        body = (await client.get("/ml/anomalies/1")).json()
    reported = {a["date"] for a in body["anomalies"]}
    assert points[10][0].isoformat() in reported
    for anomaly in body["anomalies"]:
        assert anomaly["date"] in {day.isoformat() for day, _ in points}


async def test_anomalies_without_prices_is_404() -> None:
    async with _client(FakeMlRepository([])) as client:
        response = await client.get("/ml/anomalies/999")
    assert response.status_code == 404
    assert response.json()["title"] == "Sin histórico"


async def test_anomalies_without_repository_is_503() -> None:
    async with _client(None) as client:
        response = await client.get("/ml/anomalies/1")
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Contrato transversal
# ---------------------------------------------------------------------------


async def test_ml_endpoints_are_rate_limited_like_the_rest() -> None:
    """`GET_LIMIT` también acá: el forecast es caro (statsmodels) y sin límite
    es la forma más fácil de jugar al DDoS contra la propia API."""
    budget = _limit_n()
    async with _client(FakeMlRepository(_trend(30))) as client:
        codes = [(await client.get("/ml/forecast/1")).status_code for _ in range(budget + 1)]
    assert codes.count(200) == budget
    assert codes[-1] == 429


async def test_forecast_serializes_dates_as_iso_strings() -> None:
    """El front los parsea con `new Date(...)`: un date crudo rompe el chart."""
    async with _client(FakeMlRepository(_trend(30))) as client:
        raw = (await client.get("/ml/forecast/1")).text
    body = httpx.Response(200, text=raw).json()
    for point in body["points"]:
        assert isinstance(point["date"], str)
        datetime.fromisoformat(point["date"])
