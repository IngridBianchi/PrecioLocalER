"""Integration tests de `scripts/train_forecasts.py`.

El job diario que persiste `forecasts`, `anomalies` y `ml_models` (ADR-0006).
Estaba sin cobertura y es la mitad "escrita" del ML: `app/ml/api.py` calcula
bajo demanda pero no persiste, así que si el job anda mal la app anda bien y
el comparativo Python/.NET queda vacío sin que nada falle visible.

Lo que se verifica:

1. Que itere por (producto, cadena) y no por producto: `forecasts.chain_id` y
   `ml_models.chain_id` son NOT NULL, así que la serie de mercado no se puede
   atribuir a una fila.
2. Que persista con el modelo que REALMENTE corrió, no con el pedido. Si
   sarimax degrada a Holt-Winters, en la fila tiene que decir holtwinters.
3. Que sea idempotente: correrlo dos veces no duplica filas.
4. Que `--dry-run` no escriba.
5. Que la serie que consume sea el promedio diario, no cada price_point.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.ml.models import MODEL_HOLT_WINTERS, MODEL_SARIMAX, clean_series, sarimax
from app.ml.series import build_series
from app.models import PricePointIn, ProductIn, SkuIn
from app.pipelines.dedupe import compute_price_hash
from app.pipelines.normalize import normalize_name
from scripts.train_forecasts import LANGUAGE, train_one

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

HORIZON = 7
SEASON = 7
CONTAMINATION = 0.05
BASE_KWARGS = {
    "horizon": HORIZON,
    "season_length": SEASON,
    "contamination": CONTAMINATION,
}


def _trend(
    days: int, *, base: float = 1000.0, step: float = 8.0, promo_every: int = 0
) -> list[str]:
    """Serie sintética con tendencia y finde caro: algo que un modelo pueda usar."""
    out: list[str] = []
    for i in range(days):
        price = base + step * i
        weekday = (datetime(2026, 1, 5) + timedelta(days=i)).weekday()
        if weekday in (5, 6):
            price *= 1.03
        if promo_every and i > 0 and i % promo_every == 0:
            price *= 0.85
        out.append(f"{price:.2f}")
    return out


async def _seed_series(
    repository: object,
    *,
    chain_id: int,
    name: str,
    external_id: str,
    prices: list[str],
) -> int:
    """Producto + SKU + un price_point por día. Devuelve el product_id."""
    product_id = await repository.upsert_product(  # type: ignore[attr-defined]
        ProductIn(name=name, normalized_name=normalize_name(name))
    )
    assert product_id is not None
    sku_id = await repository.upsert_sku(  # type: ignore[attr-defined]
        SkuIn(product_id=product_id, chain_id=chain_id, external_id=external_id, name=name)
    )
    assert sku_id is not None

    today = datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=0)
    for i, price in enumerate(prices):
        captured = today - timedelta(days=len(prices) - 1 - i)
        await repository.insert_price_point(  # type: ignore[attr-defined]
            PricePointIn(
                product_id=product_id,
                chain_id=chain_id,
                sku_id=sku_id,
                price=Decimal(price),
                captured_at=captured,
                raw_json={"source": "test"},
                hash=compute_price_hash(chain_id, sku_id, price, captured.isoformat()),
            )
        )
    return product_id


# ---------------------------------------------------------------------------
# Caminos de salida: por qué un par no se procesó
# ---------------------------------------------------------------------------


async def test_pair_without_prices_is_skipped(repository: object, chain_id: int) -> None:
    product_id = await repository.upsert_product(  # type: ignore[attr-defined]
        ProductIn(name="Sin History", normalized_name=normalize_name("Sin History"))
    )
    persisted, summary = await train_one(
        repository,  # type: ignore[arg-type]
        product_id=product_id,
        chain_id=chain_id,
        slug="la_anonima",
        days=0,
        model=MODEL_SARIMAX,
        dry_run=False,
        **BASE_KWARGS,
    )
    assert persisted is False
    assert "sin price_points" in summary


async def test_short_series_is_reported_not_crashed(repository: object, chain_id: int) -> None:
    """2 días no alcanzan ni para Holt-Winters: se reporta, no se rompe el lote."""
    product_id = await _seed_series(
        repository,
        chain_id=chain_id,
        name="Corto 1 l",
        external_id="SHORT-1",
        prices=_trend(2),
    )
    persisted, summary = await train_one(
        repository,  # type: ignore[arg-type]
        product_id=product_id,
        chain_id=chain_id,
        slug="la_anonima",
        days=2,
        model=MODEL_SARIMAX,
        dry_run=False,
        **BASE_KWARGS,
    )
    assert persisted is False
    assert "serie insuficiente" in summary or "sin price_points" in summary


# ---------------------------------------------------------------------------
# Lo que se persiste
# ---------------------------------------------------------------------------


async def test_train_persists_one_forecast_row_per_horizon_day(
    repository: object, chain_id: int, pg_pool: object
) -> None:
    product_id = await _seed_series(
        repository,
        chain_id=chain_id,
        name="Harina 1 kg",
        external_id="TR-1",
        prices=_trend(30, promo_every=11),
    )
    persisted, summary = await train_one(
        repository,  # type: ignore[arg-type]
        product_id=product_id,
        chain_id=chain_id,
        slug="la_anonima",
        days=30,
        model=MODEL_SARIMAX,
        dry_run=False,
        **BASE_KWARGS,
    )
    assert persisted is True, summary

    rows = await pg_pool.fetch(
        "SELECT forecast_date, predicted_price, conf_low, conf_high, horizon_days "
        "FROM forecasts WHERE product_id = $1 ORDER BY forecast_date",
        product_id,
    )
    assert len(rows) == HORIZON
    assert {r["horizon_days"] for r in rows} == {HORIZON}
    for row in rows:
        assert row["conf_low"] <= row["predicted_price"] <= row["conf_high"]


async def test_forecast_dates_are_in_the_future(
    repository: object, chain_id: int, pg_pool: object
) -> None:
    """Un forecast en el pasado es un forecast inservible."""
    product_id = await _seed_series(
        repository,
        chain_id=chain_id,
        name="Arroz 1 kg",
        external_id="TR-2",
        prices=_trend(30),
    )
    await train_one(
        repository,  # type: ignore[arg-type]
        product_id=product_id,
        chain_id=chain_id,
        slug="la_anonima",
        days=30,
        model=MODEL_SARIMAX,
        dry_run=False,
        **BASE_KWARGS,
    )
    earliest = await pg_pool.fetchval(
        "SELECT min(forecast_date) FROM forecasts WHERE product_id = $1", product_id
    )
    # `forecasts.forecast_date` es una columna date: no tiene hora.
    assert earliest > datetime.now(UTC).date()


async def test_train_persists_the_model_that_actually_ran(
    repository: object, chain_id: int, pg_pool: object
) -> None:
    """La degradación tiene que quedar en la fila, no solo en la consola.

    `sarimax` cae a Holt-Winters cuando no hay statsmodels o la serie es corta.
    El job persiste `result.model` —el que corrió— y no el pedido: si guardara
    "sarimax" cuando en realidad predijo holtwinters, el comparativo del ADR-0006
    estaría comparando dos cosas distintas y el chart mentiría.
    """
    product_id = await _seed_series(
        repository,
        chain_id=chain_id,
        name="Arroz Largo Fino 1kg",
        external_id="TR-3",
        prices=_trend(30),
    )
    await train_one(
        repository,  # type: ignore[arg-type]
        product_id=product_id,
        chain_id=chain_id,
        slug="la_anonima",
        days=30,
        model=MODEL_SARIMAX,
        dry_run=False,
        **BASE_KWARGS,
    )

    # Lo que el job corrió de verdad, reconstruido sobre la misma serie.
    points = await repository.price_series_for_forecast(product_id, "la_anonima")  # type: ignore[attr-defined]
    expected = sarimax(clean_series(build_series(points)), HORIZON, season_length=SEASON)

    stored = await pg_pool.fetch(
        "SELECT DISTINCT model FROM forecasts WHERE product_id = $1", product_id
    )
    assert [r["model"] for r in stored] == [expected.model]
    if expected.degraded_reason:
        assert expected.model == MODEL_HOLT_WINTERS


async def test_degradation_is_flagged_in_the_summary(
    repository: object, chain_id: int
) -> None:
    """El operador tiene que enterarse por stdout de que el modelo no fue el pedido."""
    product_id = await _seed_series(
        repository,
        chain_id=chain_id,
        name="Azucar 1 kg",
        external_id="TR-4",
        prices=_trend(30),
    )
    points = await repository.price_series_for_forecast(product_id, "la_anonima")  # type: ignore[attr-defined]
    expected = sarimax(clean_series(build_series(points)), HORIZON, season_length=SEASON)

    _, summary = await train_one(
        repository,  # type: ignore[arg-type]
        product_id=product_id,
        chain_id=chain_id,
        slug="la_anonima",
        days=30,
        model=MODEL_SARIMAX,
        dry_run=True,
        **BASE_KWARGS,
    )
    assert ("DEGRADADO" in summary) is bool(expected.degraded_reason)


async def test_train_marks_anomalies_without_rewriting_prices(
    repository: object, chain_id: int, pg_pool: object
) -> None:
    """Las anomalías se marcan; el price_point original no se toca ni se borra."""
    prices = _trend(30)
    prices[17] = "5.00"  # un punto absurdamente barato
    product_id = await _seed_series(
        repository,
        chain_id=chain_id,
        name="Fideos Espagueti 500g",
        external_id="TR-5",
        prices=prices,
    )
    await train_one(
        repository,  # type: ignore[arg-type]
        product_id=product_id,
        chain_id=chain_id,
        slug="la_anonima",
        days=30,
        model=MODEL_SARIMAX,
        dry_run=False,
        **BASE_KWARGS,
    )
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM price_points WHERE product_id = $1", product_id
    ) == 30, "el histórico no se toca"
    anomalies = await pg_pool.fetch(
        "SELECT detected_at, price, score FROM anomalies WHERE product_id = $1", product_id
    )
    for row in anomalies:
        assert row["detected_at"] is not None
        assert row["score"] is not None


async def test_train_persists_metrics_tagged_as_python(
    repository: object, chain_id: int, pg_pool: object
) -> None:
    """`language='python'` es lo que separa las filas de .NET en el comparativo."""
    product_id = await _seed_series(
        repository,
        chain_id=chain_id,
        name="Leche Entera 1L",
        external_id="TR-6",
        prices=_trend(40),
    )
    await train_one(
        repository,  # type: ignore[arg-type]
        product_id=product_id,
        chain_id=chain_id,
        slug="la_anonima",
        days=40,
        model=MODEL_HOLT_WINTERS,
        dry_run=False,
        **BASE_KWARGS,
    )
    rows = await pg_pool.fetch(
        "SELECT language, model, mape, rmse, mae, n_points FROM ml_models WHERE product_id = $1",
        product_id,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["language"] == LANGUAGE == "python"
    assert row["model"] == MODEL_HOLT_WINTERS
    assert row["mape"] is not None and row["mape"] >= 0
    assert row["n_points"] > 0


async def test_holt_winters_request_persists_holt_winters(
    repository: object, chain_id: int, pg_pool: object
) -> None:
    product_id = await _seed_series(
        repository,
        chain_id=chain_id,
        name="Detergente 500 ml",
        external_id="TR-7",
        prices=_trend(20),
    )
    await train_one(
        repository,  # type: ignore[arg-type]
        product_id=product_id,
        chain_id=chain_id,
        slug="la_anonima",
        days=20,
        model=MODEL_HOLT_WINTERS,
        dry_run=False,
        **BASE_KWARGS,
    )
    stored = await pg_pool.fetch(
        "SELECT DISTINCT model FROM forecasts WHERE product_id = $1", product_id
    )
    assert [r["model"] for r in stored] == [MODEL_HOLT_WINTERS]


async def test_metrics_never_overwrite_the_dotnet_row(
    repository: object, chain_id: int, pg_pool: object
) -> None:
    """Python y .NET escriben en la misma tabla: una fila no pisa a la otra."""
    product_id = await _seed_series(
        repository,
        chain_id=chain_id,
        name="Jabon Liquido 500ml",
        external_id="TR-8",
        prices=_trend(40),
    )
    await pg_pool.execute(
        """
        INSERT INTO ml_models (product_id, chain_id, model, language, mape, rmse, mae, n_points)
        VALUES ($1, $2, 'ssa', 'dotnet', 12.5, 100.0, 90.0, 19)
        """,
        product_id,
        chain_id,
    )
    await train_one(
        repository,  # type: ignore[arg-type]
        product_id=product_id,
        chain_id=chain_id,
        slug="la_anonima",
        days=40,
        model=MODEL_HOLT_WINTERS,
        dry_run=False,
        **BASE_KWARGS,
    )
    rows = await pg_pool.fetch(
        "SELECT language, mape FROM ml_models WHERE product_id = $1 ORDER BY language", product_id
    )
    assert [r["language"] for r in rows] == ["dotnet", "python"]
    dotnet = next(r for r in rows if r["language"] == "dotnet")
    assert Decimal(str(dotnet["mape"])) == Decimal("12.5")


# ---------------------------------------------------------------------------
# Idempotencia y dry-run
# ---------------------------------------------------------------------------


async def test_train_is_idempotent(repository: object, chain_id: int, pg_pool: object) -> None:
    """El job corre a diario: correrlo dos veces no puede duplicar nada."""
    product_id = await _seed_series(
        repository,
        chain_id=chain_id,
        name="Harina 000 1kg",
        external_id="TR-9",
        prices=_trend(30, promo_every=11),
    )
    kwargs = {
        "product_id": product_id,
        "chain_id": chain_id,
        "slug": "la_anonima",
        "days": 30,
        "model": MODEL_SARIMAX,
        "dry_run": False,
        **BASE_KWARGS,
    }
    await train_one(repository, **kwargs)  # type: ignore[arg-type]
    first = await _ml_counts(pg_pool, product_id)
    await train_one(repository, **kwargs)  # type: ignore[arg-type]
    second = await _ml_counts(pg_pool, product_id)
    assert first == second
    assert first["forecasts"] == HORIZON


async def _ml_counts(pg_pool: object, product_id: int) -> dict[str, int]:
    return {
        "forecasts": await pg_pool.fetchval(
            "SELECT count(*) FROM forecasts WHERE product_id = $1", product_id
        ),
        "anomalies": await pg_pool.fetchval(
            "SELECT count(*) FROM anomalies WHERE product_id = $1", product_id
        ),
        "metrics": await pg_pool.fetchval(
            "SELECT count(*) FROM ml_models WHERE product_id = $1", product_id
        ),
    }


async def test_dry_run_writes_nothing(repository: object, chain_id: int, pg_pool: object) -> None:
    product_id = await _seed_series(
        repository,
        chain_id=chain_id,
        name="Papel Higienico 4 rollos",
        external_id="TR-10",
        prices=_trend(30),
    )
    persisted, summary = await train_one(
        repository,  # type: ignore[arg-type]
        product_id=product_id,
        chain_id=chain_id,
        slug="la_anonima",
        days=30,
        model=MODEL_SARIMAX,
        dry_run=True,
        **BASE_KWARGS,
    )
    assert persisted is True
    assert "dry-run" in summary
    assert await pg_pool.fetchval("SELECT count(*) FROM forecasts") == 0
    assert await pg_pool.fetchval("SELECT count(*) FROM ml_models") == 0


async def test_dry_run_then_real_run_leaves_one_row_per_day(
    repository: object, chain_id: int, pg_pool: object
) -> None:
    """Un dry-run seguido del run real tiene que dejar el mismo estado que solo el real."""
    product_id = await _seed_series(
        repository,
        chain_id=chain_id,
        name="Cafe Molido 250g",
        external_id="TR-11",
        prices=_trend(30),
    )
    kwargs = {
        "product_id": product_id,
        "chain_id": chain_id,
        "slug": "la_anonima",
        "days": 30,
        "model": MODEL_HOLT_WINTERS,
        **BASE_KWARGS,
    }
    await train_one(repository, dry_run=True, **kwargs)  # type: ignore[arg-type]
    await train_one(repository, dry_run=False, **kwargs)  # type: ignore[arg-type]
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM forecasts WHERE product_id = $1", product_id
    ) == HORIZON


# ---------------------------------------------------------------------------
# La serie que consume el job
# ---------------------------------------------------------------------------


async def test_series_for_forecast_is_the_daily_average(
    repository: object, chain_id: int
) -> None:
    """Dos capturas del mismo día no son dos días de evidencia."""
    name = "Doble Captura"
    product_id = await repository.upsert_product(  # type: ignore[attr-defined]
        ProductIn(name=name, normalized_name=normalize_name(name))
    )
    sku_id = await repository.upsert_sku(  # type: ignore[attr-defined]
        SkuIn(product_id=product_id, chain_id=chain_id, external_id="DBL-1", name=name)
    )
    today = datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=0)
    for day in range(20):
        for offset_hours, price in ((0, "100.00"), (12, "120.00")):
            captured = today - timedelta(days=19 - day) + timedelta(hours=offset_hours)
            await repository.insert_price_point(  # type: ignore[attr-defined]
                PricePointIn(
                    product_id=product_id,
                    chain_id=chain_id,
                    sku_id=sku_id,
                    price=Decimal(price),
                    captured_at=captured,
                    raw_json={},
                    hash=compute_price_hash(
                        chain_id, sku_id, f"{price}-{day}-{offset_hours}", captured.isoformat()
                    ),
                )
            )
    points = await repository.price_series_for_forecast(product_id, "la_anonima")  # type: ignore[attr-defined]
    series = build_series(points)
    assert len(series) == 20, "40 price_points son 20 días, no 40"
    assert series.values[0] == pytest.approx(110.0), "el promedio del día, no el último"


async def test_job_ignores_the_market_series(
    repository: object, chain_id: int, pg_pool: object
) -> None:
    """La serie de mercado es válida para predecir pero no se puede atribuir a
    una fila: `chain_id` es NOT NULL, así que el job la ignora."""
    name = "Solo Una Cadena"
    product_id = await _seed_series(
        repository, chain_id=chain_id, name=name, external_id="ONE-1", prices=_trend(30)
    )
    await train_one(
        repository,  # type: ignore[arg-type]
        product_id=product_id,
        chain_id=chain_id,
        slug="la_anonima",
        days=30,
        model=MODEL_HOLT_WINTERS,
        dry_run=False,
        **BASE_KWARGS,
    )
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM forecasts WHERE product_id = $1 AND chain_id <> $2",
        product_id,
        chain_id,
    ) == 0
