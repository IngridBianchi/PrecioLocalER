using System.Runtime.InteropServices;
using Microsoft.ML;
using Microsoft.ML.Data;
using Microsoft.ML.Transforms.TimeSeries;
using PrecioLocal.Ml.Domain;

namespace PrecioLocal.Ml.Forecasting;

/// <summary>
/// SsaForecaster (ML.NET) — la "segunda opinión" del ADR-0006.
///
/// Complementa al SARIMAX/Holt-Winters de Python con otro algoritmo y otro
/// runtime. Solo se usa con serie suficiente (>= <see cref="MinPoints"/>): el
/// ADR pide que los productos con poco histórico se queden en Python, no que
/// .NET invente.
///
/// Nota de despliegue: el SVD que usa SSA es MKL. En Linux `libMklImports.so`
/// enlaza contra `libiomp5.so`, que no viene en la imagen base de .NET; el
/// Dockerfile lo instala. Si aun así falta, <see cref="IsAvailable"/> es false
/// y el servicio degrada en vez de caerse.
/// </summary>
public sealed class SsaForecastService
{
    /// <summary>Mínimo de observaciones para que SSA tenga señal.</summary>
    public const int MinPoints = 21;

    /// <summary>Nivel de confianza de la banda que pide ML.NET.</summary>
    private const float ConfidenceLevel = 0.95f;

    private const string ForecastColumn = "ForecastedPrice";
    private const string LowerBoundColumn = "ConfLow";
    private const string UpperBoundColumn = "ConfHigh";

    private readonly MLContext _ml = new(seed: 42);

    private bool? _available;

    /// <summary>
    /// Si el runtime nativo de MKL está disponible en este host. Se determina
    /// entrenando de verdad: la carga perezosa por nombre no es fiable porque
    /// NuGet deja el native en el subdirectorio del RID, no en la raíz del app.
    /// </summary>
    public bool IsAvailable => _available ??= Probe();

    /// <summary>
    /// Entrena y predice los <paramref name="horizon"/> días siguientes a la
    /// última observación. Devuelve <c>DegradedReason</c> puesto si la serie no
    /// alcanza o si el runtime nativo no está.
    /// </summary>
    public ForecastResult Forecast(PriceSeries series, int horizon, int seasonalitySize = 7)
    {
        ArgumentNullException.ThrowIfNull(series);

        if (horizon < 1)
        {
            return ForecastResult.Empty("horizon debe ser >= 1");
        }

        if (series.Length < MinPoints)
        {
            return ForecastResult.Empty($"ssaforecaster requiere >= {MinPoints} puntos, hay {series.Length}");
        }

        if (seasonalitySize < 2)
        {
            return ForecastResult.Empty("seasonalitySize debe ser >= 2");
        }

        try
        {
            return Train(series, horizon, seasonalitySize);
        }
        catch (Exception exception) when (IsNativeFailure(exception))
        {
            return ForecastResult.Empty($"MKL no disponible en este host: {exception.GetType().Name}");
        }
    }

    private static bool IsNativeFailure(Exception exception) =>
        exception is DllNotFoundException
            or TypeInitializationException
            or BadImageFormatException
            or EntryPointNotFoundException
            or TypeLoadException;

    private ForecastResult Train(PriceSeries series, int horizon, int seasonalitySize)
    {
        var data = _ml.Data.LoadFromEnumerable(
            series.Prices.Select(price => new Observation(price)));

        // ML.NET exige seriesLength > windowSize: el ciclo semanal (7) es el
        // caso por defecto del retail, y 2x windowSize da una season suficiente
        // para la trajectory matrix sin pedir más histórico del que hay.
        var estimator = _ml.Forecasting.ForecastBySsa(
            outputColumnName: ForecastColumn,
            inputColumnName: nameof(Observation.Price),
            windowSize: seasonalitySize,
            seriesLength: seasonalitySize * 2,
            trainSize: series.Length,
            horizon: horizon,
            rankSelectionMethod: RankSelectionMethod.Exact,
            confidenceLowerBoundColumn: LowerBoundColumn,
            confidenceUpperBoundColumn: UpperBoundColumn,
            confidenceLevel: ConfidenceLevel);

        var transformer = estimator.Fit(data);

        // El transformer es stateful: devuelve una fila por observación, cada
        // una con el vector de predicciones *anteriores* a ese punto. La
        // última fila es la que proyecta el horizonte tras el último dato.
        var projected = transformer.Transform(data);
        var rows = _ml.Data.CreateEnumerable<Projection>(projected, reuseRowObject: false).ToList();
        if (rows.Count == 0)
        {
            return ForecastResult.Empty("ML.NET no devolvió predicciones");
        }

        var last = rows[^1];
        var forecasts = last.Forecast;
        var lows = last.ConfLow;
        var highs = last.ConfHigh;
        var steps = Math.Min(horizon, forecasts.Length);

        var lastDay = series.Days[^1];
        var points = new List<ForecastPoint>(steps);
        for (var step = 0; step < steps; step++)
        {
            points.Add(new ForecastPoint(
                Day: lastDay.AddDays(step + 1),
                PredictedPrice: ToPrice(forecasts[step]),
                ConfLow: ToPrice(lows[step]),
                ConfHigh: ToPrice(highs[step])));
        }

        return new ForecastResult(
            Model: ModelNames.MlNet,
            Points: points,
            NumPoints: series.Length);
    }

    /// <summary>
    /// Validación walk-forward con las mismas reglas que
    /// <c>app.ml.models.walk_forward_validate</c> (nunca split aleatorio:
    /// fuga de futuro). Solo para el comparativo del ADR-0006 (WP5).
    /// </summary>
    public ForecastMetrics WalkForward(PriceSeries series, int minTrain = MinPoints, int horizon = 1, int maxFolds = 5)
    {
        ArgumentNullException.ThrowIfNull(series);

        if (series.Length <= minTrain)
        {
            throw new InvalidOperationException(
                $"walk-forward requiere > {minTrain} puntos, hay {series.Length}");
        }

        var actual = new List<float>();
        var predicted = new List<float>();

        var firstFold = Math.Max(minTrain + 1, series.Length - horizon - maxFolds + 1);
        for (var start = firstFold; start + horizon <= series.Length; start++)
        {
            var train = new PriceSeries(
                series.ProductId,
                series.ChainId,
                series.Days.Take(start).ToList(),
                series.Prices.Take(start).ToList());

            var result = Forecast(train, horizon);
            if (result.Points.Count < horizon)
            {
                continue;
            }

            for (var offset = 0; offset < horizon; offset++)
            {
                actual.Add(series.Prices[start + offset]);
                predicted.Add((float)result.Points[offset].PredictedPrice);
            }
        }

        return ComputeMetrics(actual, predicted);
    }

    /// <summary>Mismo MAPE/RMSE/MAE que <c>app.ml.models.metrics</c>.</summary>
    public static ForecastMetrics ComputeMetrics(IReadOnlyList<float> actual, IReadOnlyList<float> predicted)
    {
        var pairs = actual.Zip(predicted, (observed, forecast) => (Observed: observed, Forecast: forecast))
            .Where(pair => pair.Observed > 0)
            .ToList();

        if (pairs.Count == 0)
        {
            return new ForecastMetrics(0m, 0m, 0m, 0);
        }

        var percentageErrors = pairs
            .Select(pair => Math.Abs((double)(pair.Observed - pair.Forecast) / pair.Observed))
            .ToList();
        var mape = (decimal)(percentageErrors.Average() * 100d);
        var squared = pairs
            .Select(pair => (double)(pair.Observed - pair.Forecast) * (pair.Observed - pair.Forecast))
            .ToList();
        var rmse = (decimal)Math.Sqrt(squared.Average());
        var mae = (decimal)pairs
            .Select(pair => (double)Math.Abs(pair.Observed - pair.Forecast))
            .Average();

        return new ForecastMetrics(
            Mape: decimal.Round(mape, 4),
            Rmse: decimal.Round(rmse, 4),
            Mae: decimal.Round(mae, 4),
            NumPoints: pairs.Count);
    }

    private static decimal ToPrice(float value) =>
        decimal.Round((decimal)Math.Max(0f, value), 2);

    private bool Probe()
    {
        // Serie sintética mínima: si MKL no está, el propio SVD lanza aquí.
        var probe = Enumerable.Range(0, MinPoints).Select(i => 100f + i).ToList();
        var series = new PriceSeries(
            0,
            0,
            probe.Select((_, index) => new DateOnly(2026, 1, 1).AddDays(index)).ToList(),
            probe);

        try
        {
            return Train(series, horizon: 1, seasonalitySize: 7).Points.Count == 1;
        }
        catch (Exception exception) when (IsNativeFailure(exception))
        {
            return false;
        }
    }

    private sealed class Observation
    {
        public Observation()
        {
        }

        public Observation(float price) => Price = price;

        [LoadColumn(0)]
        public float Price { get; set; }
    }

    private sealed class Projection
    {
        [ColumnName(ForecastColumn)]
        public float[] Forecast { get; set; } = Array.Empty<float>();

        [ColumnName(LowerBoundColumn)]
        public float[] ConfLow { get; set; } = Array.Empty<float>();

        [ColumnName(UpperBoundColumn)]
        public float[] ConfHigh { get; set; } = Array.Empty<float>();
    }
}
