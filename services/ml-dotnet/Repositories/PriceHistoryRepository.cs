using Microsoft.EntityFrameworkCore;
using PrecioLocal.Ml.Data;
using PrecioLocal.Ml.Domain;

namespace PrecioLocal.Ml.Repositories;

/// <summary>
/// Lecturas de histórico para forecast y reportes. Mismo criterio que
/// <c>price_series_for_forecast</c> en Python: agregado diario en SQL, porque
/// el scraper captura varias veces por día.
/// </summary>
public sealed class PriceHistoryRepository(PriceDbContext db)
{
    /// <summary>
    /// Serie diaria de un producto, opcionalmente por cadena. Sin cadena,
    /// <see cref="PriceSeries.ChainId"/> queda en 0 (mercado completo) porque
    /// no hay una sola cadena a la que atribuir el promedio.
    /// </summary>
    public async Task<PriceSeries> SeriesAsync(
        long productId,
        string? chainSlug,
        int limit = 365,
        CancellationToken cancellationToken = default)
    {
        var chainId = chainSlug is null
            ? (long?)null
            : await ChainIdAsync(chainSlug, cancellationToken);

        if (chainSlug is not null && chainId is null)
        {
            return Empty(productId);
        }

        // Se agrupa solo por día (no por día+cadena) para que la serie sea
        // bit a bit la de `price_series_for_forecast` en Python: sin `chain`
        // promedia todas las cadenas, que es la serie de "precio de mercado".
        // Si se agrupara por cadena, cada fila sería un día-cadena y la banda
        // del ADR-0006 compararía modelos entrenados sobre series distintas.
        var rows = await (from point in db.PricePoints
                          where point.ProductId == productId
                          where chainId == null || point.ChainId == chainId
                          group point by point.CapturedAt.Date into day
                          select new { Day = day.Key, Price = day.Average(point => point.Price) })
            .OrderBy(row => row.Day)
            .Take(limit)
            .ToListAsync(cancellationToken);

        if (rows.Count == 0)
        {
            return Empty(productId);
        }

        return new PriceSeries(
            ProductId: productId,
            ChainId: chainId ?? 0,
            Days: rows.Select(row => DateOnly.FromDateTime(row.Day)).ToList(),
            Prices: rows.Select(row => (float)row.Price).ToList());
    }

    /// <summary>Resuelve el id de una cadena por slug.</summary>
    public Task<long?> ChainIdAsync(string slug, CancellationToken cancellationToken = default) =>
        db.Chains
            .Where(chain => chain.Slug == slug)
            .Select(chain => (long?)chain.Id)
            .SingleOrDefaultAsync(cancellationToken);

    /// <summary>Comprueba que el producto existe (evita 404 con serie vacía).</summary>
    public Task<bool> ProductExistsAsync(long productId, CancellationToken cancellationToken = default) =>
        db.Products.AnyAsync(product => product.Id == productId, cancellationToken);

    /// <summary>
    /// Registra la métrica de .NET para que el comparativo del ADR-0006 pueda
    /// contrastarla con la de Python. Idempotente por el UNIQUE de 008_ml.sql.
    /// </summary>
    public async Task SaveMetricsAsync(
        long productId,
        long chainId,
        ForecastMetrics metrics,
        CancellationToken cancellationToken = default)
    {
        var existing = await db.MlModels.FirstOrDefaultAsync(
            model => model.Model == ModelNames.MlNet
                && model.Language == "dotnet"
                && model.ProductId == productId
                && model.ChainId == chainId,
            cancellationToken);

        if (existing is null)
        {
            db.MlModels.Add(new MlModel
            {
                Model = ModelNames.MlNet,
                Language = "dotnet",
                ProductId = productId,
                ChainId = chainId,
                Mape = metrics.Mape,
                Rmse = metrics.Rmse,
                Mae = metrics.Mae,
                NumPoints = metrics.NumPoints,
                TrainedAt = DateTime.UtcNow,
                IsActive = true,
            });
        }
        else
        {
            existing.Mape = metrics.Mape;
            existing.Rmse = metrics.Rmse;
            existing.Mae = metrics.Mae;
            existing.NumPoints = metrics.NumPoints;
            existing.TrainedAt = DateTime.UtcNow;
            existing.IsActive = true;
        }

        await db.SaveChangesAsync(cancellationToken);
    }

    private static PriceSeries Empty(long productId) =>
        new(productId, 0, Array.Empty<DateOnly>(), Array.Empty<float>());
}
