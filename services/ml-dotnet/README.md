# services/ml-dotnet — .NET 8 Web API + ML.NET

Forecast con ML.NET (SsaForecaster) + reportes LINQ sobre PostgreSQL, con EF Core.

**Rol único (ver [ADR-0001](../../docs/adr/0001-multiples-lenguajes.md) y [ADR-0006](../../docs/adr/0006-dos-modelos-ml.md)):** segunda opinión de forecast + reportes agregados. NO scrapea, NO alerta.

## Stack

`Microsoft.ML` 3.0.1 · `Microsoft.ML.TimeSeries` 3.0.1 · `Microsoft.ML.Mkl.Redist` 3.0.1 · `EF Core` 8.0.10 · `Npgsql` 8.0.10 · xUnit

## Estructura

```
PrecioLocal.Ml.csproj
Program.cs                       # host, endpoints, rate limiting, healthcheck
Domain/Contracts.cs              # ForecastResult, PriceSeries, métricas (sin infra)
Data/PriceDbContext.cs           # EF Core → price_points, chains, products, ml_models
Repositories/PriceHistoryRepository.cs
Forecasting/SsaForecastService.cs
Reports/ReportService.cs
scripts/install-mkl-runtime.sh   # libiomp5.so (ver "Requisito: MKL")
tests/PrecioLocal.Ml.Tests/
```

## Endpoints

```
GET /health                                      incluye mklAvailable
GET /api/forecast/{productId}?chain=&horizon=    SsaForecaster (7 días por defecto, máx 30)
GET /api/forecast/{productId}/metrics?persist=   walk-forward → MAPE/RMSE/MAE
GET /api/forecast/{productId}/comparison         lo que hay en ml_models (Python vs .NET)
GET /api/reports/prices?chain=&q=&limit=         último precio + min/max/promedio
GET /api/reports/inflation?span=                 inflación por categoría
GET /api/reports/movers?span=&top=               top movers (mayores subidas/bajadas)
```

Sin histórico suficiente el forecast responde **200** con `degradedReason` y `points: []`: el ADR-0006 deja esos productos en Python, no es un error. `404` = producto o serie inexistente, `400` = parámetro fuera de rango, `503` = sin base de datos configurada.

## Requisito: MKL en Linux

El SVD que usa `SsaForecaster` es Intel MKL. En Windows `MklImports.dll` es autónomo; en Linux `libMklImports.so` (que sí llega por NuGet) enlaza contra `libiomp5.so`, que **no está** ni en `mcr.microsoft.com/dotnet/aspnet:8.0` ni en los repos de Debian. Sin él, `Fit()` lanza `DllNotFoundException` y todo forecast degrada.

`scripts/install-mkl-runtime.sh` añade el repo apt firmado de Intel oneAPI e instala `intel-oneapi-runtime-openmp` (unos pocos MB; no el MKL completo). Lo usan el Dockerfile (build y runtime) y el job `dotnet-test` de CI.

## Comandos

```bash
# build + tests (necesitan libiomp5, igual que producción)
sudo ./scripts/install-mkl-runtime.sh
dotnet build -c Release
dotnet test -c Release

# imagen
docker build -t preciolocal-ml-dotnet services/ml-dotnet
```

`build`/`test` no necesitan RID (NuGet deja los natives en `runtimes/linux-x64/native/`, que el runtime sí busca). El `publish` del Dockerfile **sí** lleva `-r linux-x64`: sin RID no se copia nada y el servicio arranca degradado.
