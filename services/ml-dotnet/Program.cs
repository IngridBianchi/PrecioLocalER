using System.Net;
using System.Text.Json.Serialization;
using System.Threading.RateLimiting;
using Microsoft.AspNetCore.HttpOverrides;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.EntityFrameworkCore;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using OpenTelemetry.Metrics;
using PrecioLocal.Ml.Data;
using PrecioLocal.Ml.Domain;
using PrecioLocal.Ml.Forecasting;
using PrecioLocal.Ml.Repositories;
using PrecioLocal.Ml.Reports;
using Prometheus;

namespace PrecioLocal.Ml;

internal static class Program
{
    private const int DefaultHorizon = 7;
    private const int MaxHorizon = 30;
    private const int DefaultReportLimit = 50;
    private const int MaxReportLimit = 200;

    public static async Task<int> Main(string[] args)
    {
        if (args.Contains("--healthcheck"))
        {
            return await ProbeAsync();
        }

        var builder = WebApplication.CreateBuilder(args);
        
        // Observabilidad: OpenTelemetry + Prometheus (comentado temporalmente por incompatibilidad de versiones OTel 1.10)
        // SetupObservability(builder);

        var databaseUrl = ResolveDatabaseUrl(builder.Configuration);
        var trustedProxy = ResolveTrustedProxy(builder.Configuration);

        // Se registra siempre: si el handler lo resuelve sin DB, el 503 lo
        // devuelve el propio guard, no un fallo de inyección de dependencias.
        builder.Services.AddDbContext<PriceDbContext>(options => options.UseNpgsql(databaseUrl ?? string.Empty));

        builder.Services.AddScoped<PriceHistoryRepository>();
        builder.Services.AddScoped<ReportService>();
        builder.Services.AddSingleton<SsaForecastService>();
        builder.Services.AddRateLimiter(AddRateLimiter);

        if (trustedProxy is not null)
        {
            // Sin esto, Kestrel descarta el X-Forwarded-For (sus KnownProxies
            // default son solo loopback) y el rate limit queda particionado por
            // la IP de nginx: un solo bucket para todo el mundo. Con esto, la
            // IP es la del cliente real y 60/min son por persona.
            builder.Services.Configure<ForwardedHeadersOptions>(options =>
            {
                options.ForwardedHeaders = ForwardedHeaders.XForwardedFor | ForwardedHeaders.XForwardedProto;
                options.KnownProxies.Add(trustedProxy);
            });
        }

        builder.Services.ConfigureHttpJsonOptions(options =>
        {
            options.SerializerOptions.PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.CamelCase;
            options.SerializerOptions.Converters.Add(new JsonStringEnumConverter());
        });

        var app = builder.Build();
        // Antes del rate limiter: la partición se calcula sobre la IP ya
        // resuelta, no sobre la del socket.
        if (trustedProxy is not null)
        {
            app.UseForwardedHeaders();
        }
        app.UseRateLimiter();
        
        MapEndpoints(app, databaseUrl);

        // Prometheus metrics endpoint (Fase 6).
        app.UseHttpMetrics();
        app.MapMetrics("/metrics");

        app.Run();
        return 0;
    }

    private static void SetupObservability(WebApplicationBuilder builder)
    {
        // Comentado temporalmente por incompatibilidad de versiones OpenTelemetry 1.10
        // TODO: Reimplementar con APIs compatibles de OTel 1.10+
        var otelEndpoint = builder.Configuration["OTEL_EXPORTER_OTLP_ENDPOINT"];
        var otelHeaders = builder.Configuration["OTEL_EXPORTER_OTLP_HEADERS"];

        if (!string.IsNullOrWhiteSpace(otelEndpoint))
        {
            // TODO: Reimplementar con APIs compatibles de OTel 1.10+
        }
    }

    /// <summary>
    /// IP del proxy de entrada (nginx) que puede firmar <c>X-Forwarded-For</c>.
    /// Viene de <c>TRUSTED_PROXY_IP</c>; null si no está o no parsea, en cuyo
    /// caso no se registra el middleware y el servicio sigue funcionando (con
    /// el bucket global de siempre, que es el comportamiento previo).
    /// </summary>
    private static IPAddress? ResolveTrustedProxy(IConfiguration configuration)
    {
        var configured = configuration["TRUSTED_PROXY_IP"];
        if (string.IsNullOrWhiteSpace(configured))
        {
            return null;
        }

        return IPAddress.TryParse(configured.Trim(), out var address) ? address : null;
    }

    private static string? ResolveDatabaseUrl(IConfiguration configuration)
    {
        var configured = configuration.GetConnectionString("Postgres");
        if (string.IsNullOrWhiteSpace(configured))
        {
            configured = Environment.GetEnvironmentVariable("DATABASE_URL");
        }

        return string.IsNullOrWhiteSpace(configured) ? null : ToNpgsqlConnectionString(configured);
    }

    /// <summary>
    /// El scraper (Python) usa URI estilo SQLAlchemy
    /// (<c>postgresql://user:pass@host:5432/db</c>); Npgsql solo acepta
    /// <c>key=value</c>. Se traduce para compartir una sola variable de
    /// entorno entre los dos servicios.
    /// </summary>
    private static string ToNpgsqlConnectionString(string value)
    {
        if (!value.StartsWith("postgres://", StringComparison.OrdinalIgnoreCase)
            && !value.StartsWith("postgresql://", StringComparison.OrdinalIgnoreCase))
        {
            return value;
        }

        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri))
        {
            throw new InvalidOperationException("DATABASE_URL no es una URI válida");
        }

        var userInfo = uri.UserInfo.Split(':', 2);
        var parts = new List<string>(6)
        {
            $"Host={uri.Host}",
            $"Port={uri.Port}",
            $"Database={uri.AbsolutePath.TrimStart('/')}",
        };

        if (userInfo.Length > 0 && userInfo[0].Length > 0)
        {
            parts.Add($"Username={Uri.UnescapeDataString(userInfo[0])}");
        }

        if (userInfo.Length > 1)
        {
            parts.Add($"Password={Uri.UnescapeDataString(userInfo[1])}");
        }

        if (uri.Query.Length > 1)
        {
            parts.Add(uri.Query.TrimStart('?').Replace('&', ';'));
        }

        return string.Join(';', parts);
    }

    private static void AddRateLimiter(RateLimiterOptions options)
    {
        // docs/security.md §3: .NET = 60 req/min por IP. Ventana fija
        // particionada por IP de cliente. OJO: `RemoteIpAddress` solo es la IP
        // real si `UseForwardedHeaders` corrió antes (ver ResolveTrustedProxy);
        // sin eso era la IP de nginx y un único bucket para todos.
        options.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
        options.AddPolicy("ml", context =>
            RateLimitPartition.GetFixedWindowLimiter(
                partitionKey: context.Connection.RemoteIpAddress?.ToString() ?? "unknown",
                factory: _ => new FixedWindowRateLimiterOptions
                {
                    PermitLimit = 60,
                    Window = TimeSpan.FromMinutes(1),
                    QueueLimit = 0,
                    AutoReplenishment = true,
                }));
    }

    private static void MapEndpoints(WebApplication app, string? databaseUrl)
    {
        app.MapGet("/health", () => Results.Ok(new
        {
            status = "ok",
            service = "ml-dotnet",
            mklAvailable = app.Services.GetRequiredService<SsaForecastService>().IsAvailable,
        }));

        app.MapGet("/api/forecast/{productId:long}", async (
            long productId,
            string? chain,
            int? horizon,
            PriceHistoryRepository history,
            SsaForecastService forecaster,
            CancellationToken cancellationToken) =>
        {
            if (databaseUrl is null)
            {
                return NoDatabase();
            }

            if (horizon is > MaxHorizon or < 1)
            {
                return Invalid($"horizon debe estar entre 1 y {MaxHorizon}");
            }

            if (!await history.ProductExistsAsync(productId, cancellationToken))
            {
                return NotFound($"producto {productId} no existe");
            }

            var series = await history.SeriesAsync(productId, chain, cancellationToken: cancellationToken);
            if (series.Length == 0)
            {
                return NotFound(chain is null
                    ? $"producto {productId} no tiene price_points"
                    : $"producto {productId} no tiene price_points para la cadena '{chain}'");
            }

            var result = forecaster.Forecast(series, horizon ?? DefaultHorizon);

            // Serie insuficiente no es error HTTP: el ADR dice que .NET no
            // aplica a productos con poco histórico y lo reporta a Python.
            return Results.Ok(new
            {
                productId,
                chain,
                model = result.Model,
                degradedReason = result.DegradedReason,
                nPoints = result.NumPoints,
                points = result.Points.Select(point => new
                {
                    date = point.Day.ToString("yyyy-MM-dd"),
                    predictedPrice = point.PredictedPrice,
                    confLow = point.ConfLow,
                    confHigh = point.ConfHigh,
                }),
            });
        })
        .RequireRateLimiting("ml");

        app.MapGet("/api/forecast/{productId:long}/metrics", async (
            long productId,
            string? chain,
            int? horizon,
            bool? persist,
            PriceHistoryRepository history,
            SsaForecastService forecaster,
            CancellationToken cancellationToken) =>
        {
            if (databaseUrl is null)
            {
                return NoDatabase();
            }

            var series = await history.SeriesAsync(productId, chain, cancellationToken: cancellationToken);
            if (series.Length == 0)
            {
                return NotFound($"producto {productId} no tiene histórico para validar");
            }

            ForecastMetrics metrics;
            try
            {
                metrics = forecaster.WalkForward(series, horizon: horizon ?? 1);
            }
            catch (InvalidOperationException exception)
            {
                return Results.Problem(
                    title: "Serie insuficiente",
                    detail: exception.Message,
                    statusCode: StatusCodes.Status422UnprocessableEntity);
            }

            // `ml_models.chain_id` es NOT NULL REFERENCES chains(id): sin
            // cadena resuelta la métrica es de mercado y no se puede atribuir
            // a una fila, así que se calcula pero no se persiste.
            var persisted = false;
            if (persist == true && series.ChainId > 0)
            {
                await history.SaveMetricsAsync(productId, series.ChainId, metrics, cancellationToken);
                persisted = true;
            }

            return Results.Ok(new
            {
                productId,
                chain,
                model = ModelNames.MlNet,
                mape = metrics.Mape,
                rmse = metrics.Rmse,
                mae = metrics.Mae,
                nPoints = metrics.NumPoints,
                persisted,
            });
        })
        .RequireRateLimiting("ml");

        app.MapGet("/api/forecast/{productId:long}/comparison", async (
            long productId,
            long? chainId,
            ReportService reports,
            CancellationToken cancellationToken) =>
        {
            if (databaseUrl is null)
            {
                return NoDatabase();
            }

            return Results.Ok(await reports.ModelComparisonAsync(productId, chainId, cancellationToken));
        })
        .RequireRateLimiting("ml");

        app.MapGet("/api/reports/prices", async (
            string? chain,
            string? q,
            int? limit,
            ReportService reports,
            CancellationToken cancellationToken) =>
        {
            if (databaseUrl is null)
            {
                return NoDatabase();
            }

            if (limit is > MaxReportLimit or < 1)
            {
                return Invalid($"limit debe estar entre 1 y {MaxReportLimit}");
            }

            return Results.Ok(await reports.PricesAsync(
                chain,
                q,
                limit ?? DefaultReportLimit,
                cancellationToken));
        })
        .RequireRateLimiting("ml");

        app.MapGet("/api/reports/inflation", async (
            string? span,
            ReportService reports,
            CancellationToken cancellationToken) =>
        {
            if (databaseUrl is null)
            {
                return NoDatabase();
            }

            var resolvedSpan = string.IsNullOrWhiteSpace(span) ? "30d" : span;
            return Results.Ok(await reports.InflationAsync(resolvedSpan, cancellationToken));
        })
        .RequireRateLimiting("ml");

        app.MapGet("/api/reports/movers", async (
            string? span,
            int? top,
            ReportService reports,
            CancellationToken cancellationToken) =>
        {
            if (databaseUrl is null)
            {
                return NoDatabase();
            }

            if (top is > MaxReportLimit or < 1)
            {
                return Invalid($"top debe estar entre 1 y {MaxReportLimit}");
            }

            var resolvedSpan = string.IsNullOrWhiteSpace(span) ? "30d" : span;
            return Results.Ok(await reports.TopMoversAsync(resolvedSpan, top ?? 20, cancellationToken));
        })
        .RequireRateLimiting("ml");
    }

    private static IResult NoDatabase() => Results.Problem(
        title: "Base de datos no configurada",
        detail: "Falta POSTGRES/DATABASE_URL para este servicio",
        statusCode: StatusCodes.Status503ServiceUnavailable);

    private static IResult NotFound(string detail) => Results.Problem(
        title: "No encontrado",
        detail: detail,
        statusCode: StatusCodes.Status404NotFound);

    private static IResult Invalid(string detail) => Results.Problem(
        title: "Petición inválida",
        detail: detail,
        statusCode: StatusCodes.Status400BadRequest);

    private static async Task<int> ProbeAsync()
    {
        using var probe = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };
        using var response = await probe.GetAsync("http://localhost:8001/health");
        return response.IsSuccessStatusCode ? 0 : 1;
    }
}
