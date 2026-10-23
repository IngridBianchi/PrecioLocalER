using System;
using System.Collections.Generic;
using System.Linq;
using PrecioLocal.Ml.Domain;
using PrecioLocal.Ml.Forecasting;
using Xunit;

namespace PrecioLocal.Ml.Tests;

public sealed class SsaForecastServiceTests
{
    private static readonly SsaForecastService Service = new();

    private static PriceSeries Build(params float[] prices) => new(
        ProductId: 1,
        ChainId: 1,
        Days: prices.Select((_, index) => new DateOnly(2026, 1, 1).AddDays(index)).ToList(),
        Prices: prices);

    private static PriceSeries Trending(int length = 30)
    {
        var prices = new float[length];
        for (var i = 0; i < length; i++)
        {
            prices[i] = 100f + i;
        }

        return Build(prices);
    }

    [Fact]
    public void MklRuntimeIsAvailable()
    {
        Assert.True(
            Service.IsAvailable,
            "SSA hace SVD con MKL: faltan libMklImports.so y/o libiomp5.so. Ver el README de services/ml-dotnet.");
    }

    [Fact]
    public void Forecast_ReturnsRequestedHorizon()
    {
        var result = Service.Forecast(Trending(), horizon: 5);

        Assert.Equal(ModelNames.MlNet, result.Model);
        Assert.Equal(5, result.Points.Count);
        Assert.Null(result.DegradedReason);
    }

    [Fact]
    public void Forecast_DaysAreContiguousAfterLastObservation()
    {
        var series = Trending();
        var result = Service.Forecast(series, horizon: 3);

        Assert.Equal(series.Days[^1].AddDays(1), result.Points[0].Day);
        Assert.Equal(series.Days[^1].AddDays(3), result.Points[2].Day);
    }

    [Fact]
    public void Forecast_BandIsOrderedAndNonNegative()
    {
        var result = Service.Forecast(Trending(), horizon: 4);

        foreach (var point in result.Points)
        {
            Assert.True(point.ConfLow >= 0m);
            Assert.True(point.ConfLow <= point.PredictedPrice);
            Assert.True(point.PredictedPrice <= point.ConfHigh);
        }
    }

    [Fact]
    public void Forecast_DegradesWhenSeriesTooShort()
    {
        var result = Service.Forecast(Build(10f, 11f, 12f), horizon: 7);

        Assert.Equal(ModelNames.MlNet, result.Model);
        Assert.Empty(result.Points);
        Assert.NotNull(result.DegradedReason);
        Assert.Contains("21 puntos", result.DegradedReason);
    }

    [Fact]
    public void Forecast_RejectsInvalidSeasonality()
    {
        var result = Service.Forecast(Trending(), horizon: 3, seasonalitySize: 1);

        Assert.Empty(result.Points);
        Assert.Contains("seasonalitySize", result.DegradedReason!);
    }

    [Fact]
    public void Forecast_IsStableAcrossCalls()
    {
        // SSA con selección de rango automática no promete bit-determinismo:
        // el SVD puede variar en la última cifra. Lo que sí debe cumplirse es
        // que dos llamadas sobre la misma serie no se desvien.
        var first = Service.Forecast(Trending(), horizon: 5);
        var second = Service.Forecast(Trending(), horizon: 5);

        Assert.Equal(first.Points.Count, second.Points.Count);
        for (var i = 0; i < first.Points.Count; i++)
        {
            Assert.True(
                Math.Abs((double)(first.Points[i].PredictedPrice - second.Points[i].PredictedPrice)) < 0.05,
                $"paso {i}: {first.Points[i].PredictedPrice} vs {second.Points[i].PredictedPrice}");
        }
    }

    [Fact]
    public void WalkForward_ReturnsMetricsForTrendingSeries()
    {
        var result = Service.WalkForward(Trending(40));

        Assert.True(result.NumPoints > 0);
        Assert.True(result.Mape >= 0m);
    }

    [Fact]
    public void WalkForward_RejectsShortHistory()
    {
        Assert.Throws<InvalidOperationException>(
            () => Service.WalkForward(Build(1f, 2f, 3f)));
    }
}

public sealed class MetricsTests
{
    [Fact]
    public void PerfectPredictionHasZeroError()
    {
        var metrics = SsaForecastService.ComputeMetrics(
            new[] { 100f, 200f },
            new[] { 100f, 200f });

        Assert.Equal(0m, metrics.Mape);
        Assert.Equal(0m, metrics.Rmse);
        Assert.Equal(0m, metrics.Mae);
    }

    [Fact]
    public void KnownErrorMatchesPythonImplementation()
    {
        // app.ml.models.metrics([100.0], [110.0]) == (10.0, 10.0, 10.0)
        var metrics = SsaForecastService.ComputeMetrics(
            new[] { 100f },
            new[] { 110f });

        Assert.Equal(10.0m, metrics.Mape, 4);
        Assert.Equal(10.0m, metrics.Rmse, 4);
        Assert.Equal(10.0m, metrics.Mae, 4);
    }

    [Fact]
    public void ZeroActualsAreIgnored()
    {
        var metrics = SsaForecastService.ComputeMetrics(
            new[] { 0f, 100f },
            new[] { 50f, 100f });

        Assert.Equal(0m, metrics.Mape);
        Assert.Equal(1, metrics.NumPoints);
    }

    [Fact]
    public void EmptyInputsReturnZeroedMetrics()
    {
        var metrics = SsaForecastService.ComputeMetrics(
            Array.Empty<float>(),
            Array.Empty<float>());

        Assert.Equal(0m, metrics.Mape);
        Assert.Equal(0, metrics.NumPoints);
    }
}
