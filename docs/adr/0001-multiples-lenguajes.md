# ADR-0001: Múltiples lenguajes en el backend

## Estado

Aceptado · 2026-09-23

## Contexto

El plan original pedía demostrar Python, .NET y Java en un mismo proyecto. La pregunta correcta no es "¿cuántas tecnologías puedo meter?", sino "¿cada una justifica su puesto?" Un monorepo con 3 lenguajes sin razón duplica mantenimiento y CI. Uno con 3 lenguajes con bounded contexts claros demuestra criterio.

## Decisión

Un lenguaje por **bounded context**, elegido por el ajuste natural al problema:

| Bounded context | Lenguaje | Por qué ajusta |
|---|---|---|
| Scraping + ML estadístico | Python 3.12 + FastAPI | Ecosistema maduro: `httpx` async, BeautifulSoup, `scikit-learn`, `statsmodels`, `pandas`; iterate rápido sobre HTML cambiante |
| Forecast + reportes | .NET 8 + ML.NET | ML.NET entrena y sirve el modelo (SsaForecaster) en el mismo runtime; EF Core + LINQ para reportes agregados sobre SQL |
| Alertas reactivas + tiempo real | Java 17 + Spring Boot 3 WebFlux | Fluxos reactivos (R2DBC + MongoDB Reactive) con backpressure natural para streams de eventos; WebSocket STOMP robusto |
| API unificada | Node 24 + Apollo Server 4 | El ecosistema JS es donde vive GraphQL/DataLoader/subscriptions sin fricción |

Los servicios **se comunican solo por contrato** (`docs/contracts.md`), nunca por código compartido. Se reconocen los tradeoffs: CI por lenguaje y mayor superficie de conocimientos.

## Alternativas consideradas

| Alternativa | Pros | Contras | Por qué no |
|---|---|---|---|
| Todo en Node | Un solo runtime, CI simple | No demuestra ML.NET ni WebFlux; Node es débil para ML/forecasting serio | No cumple el objetivo portfolio |
| Todo en Python | Un solo lenguaje, ML natural | No demuestra .NET ni Java | Idem |
| Backend único en Java | Un solo servicio, menos infra | WebFlux para todo sobre-ingeniería el scraper y el ML | Idem |
| Porypolyglot sin bounded contexts | Demuestra más lenguajes | Solapamiento funcional, CI gigante | La complejidad no se justifica |

## Consecuencias

- **Positivas:** cada pieza demuestra una fortaleza distinta; el rol de cada componente es defendible en entrevista; competencia técnica multiparadigma (async/await C#, reactive Java, async Python).
- **Negativas:** 3 pipelines de build distintas; más dependencias para mantener; el gateway necesariamente orquesta 3 servicios distintos.
- **Neutras:** los despliegues de free tier suelen aislar cada servicio, lo que coincide con la estructura.

## Referencias

- [docs/architecture.md](../architecture.md) — rol único por pieza
- [docs/contracts.md](../contracts.md) — contrato inter-servicio