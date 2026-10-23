namespace PrecioLocal.Ml.Domain;

/// <summary>
/// Contrato de salida del forecast. Deliberadamente idéntico en forma al de
/// Python (<c>app/ml/api.py</c>): el gateway puede mergear ambos sin traducción.
/// </summary>
public sealed record ForecastPoint(DateOnly Day, decimal PredictedPrice, decimal ConfLow, decimal ConfHigh);

public sealed record ForecastResult(
    string Model,
    IReadOnlyList<ForecastPoint> Points,
    decimal? Mape = null,
    decimal? Rmse = null,
    int NumPoints = 0,
    string? DegradedReason = null)
{
    public static ForecastResult Empty(string reason) =>
        new(ModelNames.MlNet, Array.Empty<ForecastPoint>(), DegradedReason: reason);
}

public static class ModelNames
{
    public const string MlNet = "mlnet";
    public const string Sarimax = "sarimax";
    public const string HoltWinters = "holtwinters";
}

/// <summary>Serie temporal de un producto (precio diario observado).</summary>
public sealed record PriceSeries(
    long ProductId,
    long ChainId,
    IReadOnlyList<DateOnly> Days,
    IReadOnlyList<float> Prices)
{
    public int Length => Prices.Count;
}

/// <summary>Métricas de la validación walk-forward (mismo cálculo que Python).</summary>
public sealed record ForecastMetrics(decimal Mape, decimal Rmse, decimal Mae, int NumPoints);

/// <summary>Inflación por categoría.</summary>
public sealed record CategoryInflationReport(
    string CategoryName,
    string CategorySlug,
    int ProductCount,
    decimal Inflation,
    decimal AvgPriceChange);

/// <summary>Reporte de inflación.</summary>
public sealed record InflationReportResult(
    string Period,
    string Span,
    IReadOnlyList<CategoryInflationReport> Categories,
    decimal OverallInflation,
    DateTime GeneratedAt);

/// <summary>Movimiento de precio por producto/cadena.</summary>
public sealed record PriceMoverReport(
    long ProductId,
    string ProductName,
    long ChainId,
    string ChainName,
    decimal CurrentPrice,
    decimal PreviousPrice,
    decimal PriceChange,
    decimal PriceChangePct);

/// <summary>Top movers.</summary>
public sealed record TopMoversReportResult(
    string Period,
    string Span,
    IReadOnlyList<PriceMoverReport> Gainers,
    IReadOnlyList<PriceMoverReport> Losers,
    DateTime GeneratedAt);

/// <summary>Fila de reporte agregado por cadena/producto.</summary>
public sealed record ChainPriceReport(
    long ChainId,
    string ChainSlug,
    string ChainName,
    long ProductId,
    string ProductName,
    decimal LastPrice,
    DateTime? LastCapturedAt,
    decimal? MinPrice,
    decimal? MaxPrice,
    decimal? AvgPrice,
    int Samples);

/// <summary>Registro de <c>ml_models</c>: métricas por modelo para el comparativo.</summary>
public sealed record ModelComparisonRow(
    string Model,
    string Language,
    decimal? Mape,
    decimal? Rmse,
    decimal? Mae,
    int NumPoints,
    DateTime TrainedAt);
