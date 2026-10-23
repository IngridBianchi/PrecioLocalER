using Microsoft.EntityFrameworkCore;
using PrecioLocal.Ml.Data;
using PrecioLocal.Ml.Domain;

namespace PrecioLocal.Ml.Reports;

/// <summary>
/// Reportes agregados con LINQ de EF Core (el motivo de que .NET esté en el
/// proyecto según ADR-0001: LINQ sobre SQL se lee como la consulta que es).
/// </summary>
public sealed class ReportService(PriceDbContext db)
{
    /// <summary>
    /// Último precio y estadísticas por (cadena, producto). Filtros opcionales
    /// para que el frontend no traiga el histórico completo.
    /// </summary>
    public async Task<IReadOnlyList<ChainPriceReport>> PricesAsync(
        string? chainSlug,
        string? query,
        int limit,
        CancellationToken cancellationToken = default)
    {
        var normalized = Normalize(query);

        var rows = await (from point in db.PricePoints
                          join chain in db.Chains on point.ChainId equals chain.Id
                          join product in db.Products on point.ProductId equals product.Id
                          where chainSlug == null || chain.Slug == chainSlug
                          where normalized == null || product.NormalizedName.Contains(normalized)
                          group point by new
                          {
                              chain.Id,
                              chain.Slug,
                              chain.Name,
                              ProductId = product.Id,
                              ProductName = product.Name,
                          } into grouped
                          select new
                          {
                              grouped.Key.Id,
                              grouped.Key.Slug,
                              grouped.Key.Name,
                              grouped.Key.ProductId,
                              grouped.Key.ProductName,
                              LastPrice = grouped.Max(point => point.Price),
                              LastCapturedAt = grouped.Max(point => point.CapturedAt),
                              MinPrice = grouped.Min(point => point.Price),
                              MaxPrice = grouped.Max(point => point.Price),
                              AvgPrice = grouped.Average(point => point.Price),
                              Samples = grouped.Count(),
                          })
            .OrderByDescending(row => row.LastCapturedAt)
            .Take(limit)
            .ToListAsync(cancellationToken);

        return rows
            .Select(row => new ChainPriceReport(
                ChainId: row.Id,
                ChainSlug: row.Slug,
                ChainName: row.Name,
                ProductId: row.ProductId,
                ProductName: row.ProductName,
                LastPrice: row.LastPrice,
                LastCapturedAt: row.LastCapturedAt,
                MinPrice: row.MinPrice,
                MaxPrice: row.MaxPrice,
                AvgPrice: row.AvgPrice,
                Samples: row.Samples))
            .ToList();
    }

    /// <summary>Comparativa de lo ya registrado en <c>ml_models</c> (ADR-0006).</summary>
    public async Task<IReadOnlyList<ModelComparisonRow>> ModelComparisonAsync(
        long productId,
        long? chainId,
        CancellationToken cancellationToken = default)
    {
        var query = db.MlModels
            .Where(model => model.ProductId == productId && model.IsActive)
            .Where(model => chainId == null || model.ChainId == chainId)
            .OrderBy(model => model.Mape);

        var rows = await query.ToListAsync(cancellationToken);

        return rows
            .Select(model => new ModelComparisonRow(
                Model: model.Model,
                Language: model.Language,
                Mape: model.Mape,
                Rmse: model.Rmse,
                Mae: model.Mae,
                NumPoints: model.NumPoints,
                TrainedAt: model.TrainedAt))
            .ToList();
    }

    public async Task<InflationReportResult> InflationAsync(
        string span,
        CancellationToken cancellationToken = default)
    {
        var days = ParseSpan(span);
        var since = DateTime.UtcNow.AddDays(-days);

        var pricePoints = await db.PricePoints
            .Where(point => point.CapturedAt >= since)
            .ToListAsync(cancellationToken);

        var products = await db.Products
            .ToListAsync(cancellationToken);
        // EF Core no tiene Include de Category configurado; se carga aparte.
        var categories = await db.Categories.ToListAsync(cancellationToken);
        var categoryById = categories.ToDictionary(category => category.Id);

        var pointsByProduct = pricePoints
            .GroupBy(point => point.ProductId)
            .ToDictionary(group => group.Key, group => group.OrderBy(point => point.CapturedAt).ToList());

        var productChanges = new List<(long ProductId, long? CategoryId, decimal ChangePct)>();
        foreach (var product in products)
        {
            if (!pointsByProduct.TryGetValue(product.Id, out var ordered) || ordered.Count < 2)
            {
                continue;
            }
            var first = ordered.First().Price;
            var last = ordered.Last().Price;
            if (first == 0)
            {
                continue;
            }
            var changePct = (last - first) / first * 100m;
            productChanges.Add((product.Id, product.CategoryId, changePct));
        }

        var byCategory = productChanges
            .GroupBy(change => change.CategoryId)
            .Select(group =>
            {
                var category = group.Key.HasValue && categoryById.TryGetValue(group.Key.Value, out var found)
                    ? found
                    : null;
                var avg = group.Average(change => change.ChangePct);
                return new CategoryInflationReport(
                    CategoryName: category?.Name ?? "Sin categoría",
                    CategorySlug: category?.Slug ?? "sin-categoria",
                    ProductCount: group.Count(),
                    Inflation: Math.Round(avg, 2),
                    AvgPriceChange: Math.Round(avg, 2));
            })
            .OrderByDescending(report => report.Inflation)
            .ToList();

        var overall = byCategory.Count == 0
            ? 0m
            : Math.Round(byCategory.Average(report => report.Inflation), 2);

        return new InflationReportResult(
            Period: "MONTH",
            Span: span,
            Categories: byCategory,
            OverallInflation: overall,
            GeneratedAt: DateTime.UtcNow);
    }

    public async Task<TopMoversReportResult> TopMoversAsync(
        string span,
        int top,
        CancellationToken cancellationToken = default)
    {
        var days = ParseSpan(span);
        var since = DateTime.UtcNow.AddDays(-days);

        var pricePoints = await db.PricePoints
            .Where(point => point.CapturedAt >= since)
            .ToListAsync(cancellationToken);

        var products = await db.Products.ToListAsync(cancellationToken);
        var chains = await db.Chains.ToListAsync(cancellationToken);
        var productById = products.ToDictionary(product => product.Id);
        var chainById = chains.ToDictionary(chain => chain.Id);

        var movers = pricePoints
            .GroupBy(point => new { point.ProductId, point.ChainId })
            .Select(group =>
            {
                var ordered = group.OrderBy(point => point.CapturedAt).ToList();
                if (ordered.Count < 2)
                {
                    return null;
                }
                var first = ordered.First();
                var last = ordered.Last();
                if (first.Price == 0)
                {
                    return null;
                }
                var change = last.Price - first.Price;
                var pct = change / first.Price * 100m;
                var product = productById.GetValueOrDefault(first.ProductId);
                var chain = chainById.GetValueOrDefault(first.ChainId);
                return new PriceMoverReport(
                    ProductId: first.ProductId,
                    ProductName: product?.Name ?? $"Producto {first.ProductId}",
                    ChainId: first.ChainId,
                    ChainName: chain?.Name ?? $"Cadena {first.ChainId}",
                    CurrentPrice: last.Price,
                    PreviousPrice: first.Price,
                    PriceChange: change,
                    PriceChangePct: Math.Round(pct, 2));
            })
            .Where(mover => mover is not null)
            .Cast<PriceMoverReport>()
            .ToList();

        var gainers = movers
            .Where(mover => mover.PriceChangePct > 0)
            .OrderByDescending(mover => mover.PriceChangePct)
            .Take(top)
            .ToList();

        var losers = movers
            .Where(mover => mover.PriceChangePct < 0)
            .OrderBy(mover => mover.PriceChangePct)
            .Take(top)
            .ToList();

        return new TopMoversReportResult(
            Period: "MONTH",
            Span: span,
            Gainers: gainers,
            Losers: losers,
            GeneratedAt: DateTime.UtcNow);
    }

    private static int ParseSpan(string span)
    {
        var value = span.Trim().ToLowerInvariant();
        if (value.EndsWith("d"))
        {
            value = value[..^1];
        }
        return int.TryParse(value, out var days) && days > 0 ? days : 30;
    }

    private static string? Normalize(string? query) =>
        string.IsNullOrWhiteSpace(query) ? null : query.Trim().ToLowerInvariant();
}
