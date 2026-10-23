# ADR-0007: Cold start de Render asumido conscientemente

## Estado

Aceptado · 2026-10-06

## Contexto

Los servicios backend (scraper, ml-dotnet, analytics-java, gateway) se despliegan en **Render Free Tier** (o equivalentemente Railway/Cloud Run free tiers). Estas plataformas apagan las instancias tras **~15 min de inactividad** para ahorrar recursos. La siguiente request dispara un *cold start*:

| Servicio | Tiempo estimado cold start | Causa principal |
|---|---|---|
| Python (FastAPI) | 3–8 s | Import de `pydantic`, `sqlalchemy`, `httpx`, `bs4`, modelo ML opcional |
| .NET 8 (ML.NET) | 8–20 s | JIT + carga de `Microsoft.ML` + MKL + `libiomp5.so` |
| Java (Spring WebFlux) | 10–25 s | Classloading Spring + R2DBC pool + Reactor + MongoDB driver |
| Node (Apollo) | 2–5 s | `require` de `@apollo/server`, `graphql`, `dataloader` |

El *free tier* es una **restricción presupuestaria real** (costo cero) que no se puede ignorar en un portfolio personal. La alternativa (servidor VPS siempre encendido) cuesta USD 5–10/mes y rompe la regla "costo cero" del proyecto.

## Decisión

**Asumir el cold start como propiedad del sistema**, no como bug, y diseñar la UX para mitigarlo honestamente:

1. **Health checks expuestos y rápidos** (`/health` < 500 ms, `/ready` con checks de DB) para que load balancers / uptime monitors no marquen el servicio como *down* durante el arranque.
2. **Estados de carga en el frontend** (skeletons, spinners, mensajes "Despertando servicio...") en lugar de fallos silenciosos o timeouts genéricos.
3. **No ping artificial** (no cron job externo que haga `curl /health` cada 10 min): gasta cuota de la plataforma, oculta el problema real y no escala.
4. **Documentación en README y ADR**: el evaluador técnico ve que *sabés* que existe, *por qué* existe, y *cómo* lo mitigás.

## Alternativas consideradas

| Alternativa | Pros | Contras | Por qué no |
|---|---|---|---|
| Ping externo (cron/uptimerobot) cada 5 min | Evita cold start | Gasta cuota free tier; ocusta deuda técnica; no soluciona primera request tras deploy | Violenta restricción de costo y honestidad |
| Migrar a VPS (Hetzner/DigitalOcean) | Sin cold start | Costo USD 5–10/mes; mantenimiento de OS; rompe "costo cero" | Fuera de scope del portfolio |
| Cloudflare Workers / Edge | Cold start ~0 ms | No soporta WebSockets STOMP ni ML.NET nativo; vendor lock-in | Arquitectura incompatible |
| Render paid tier | Siempre encendido | USD 7/mes por servicio × 4 = USD 28/mes | Fuera de presupuesto |

## Consecuencias

- **Positivas**: Costo cero real; arquitectura honesta; demuestra criterio de *trade-offs* en entrevistas.
- **Negativas**: Primera request tras inactividad tarda 3–25 s; el usuario ve spinner/skeleton.
- **Neutras**: Requiere `docs/deploy.md` y README que expliquen la UX compensatoria.

## Implementación de la UX compensatoria (Fase 5/6)

| Capa | Qué se muestra | Tiempo |
|---|---|---|
| **Angular (Apollo Link)** | Interceptor que detecta `503` / timeout > 10 s → banner "Despertando servicio, reintentando..." + retry exponencial | 0–25 s |
| **Skeletons** | Dashboard, Comparador, Predicciones muestran placeholders mientras cargan | 0–3 s |
| **Error boundary** | Si tras 3 retries falla → mensaje "El servicio está despertando, probá en unos segundos" + botón reintentar | > 25 s |
| **Health endpoint** | `/health` responde 200 inmediato (no toca DB); `/ready` tarda lo que tarde el pool de conexiones | < 500 ms / 1–2 s |

## Referencias

- [Render Free Tier Limits](https://render.com/docs/free#spinning-down)
- [Railway Sleep Policy](https://docs.railway.app/manage/services/sleep)
- [ADR-0001-multiples-lenguajes](0001-multiples-lenguajes.md) — justifica 4 lenguajes
- [docs/deploy.md](../deploy.md) — pasos de deploy con health checks