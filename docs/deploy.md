# Deploy — PrecioLocal ER (100% gratuito)

> **Estado (2026-10-20):** los 5 servicios backend + frontend compilan, pasan tests y corren localmente **8/8 healthy**. El deploy a free tiers **no empezó**: no hay cuentas ni secretos creados. Este documento es la guía para ejecutarlo cuando el usuario lo pida.

Guía paso a paso para llevar el proyecto a free tiers. Se completa con detalle real a medida que cada servicio se implementa.

## 1. Mapa de destinos

Estado real al 2026-10-20: existen los 5 servicios backend y el frontend, todos
con tests verdes en local. El deploy sigue bloqueado por falta de cuentas y
secretos; las filas de abajo son el plan de ejecución, no un estado publicado.

| Componente | Estado | Destino | Free tier | Observación |
|---|---|---|---|---|
| Scraper (Python) | ✅ local | Render | 512 MB / mes | Web Service; se duerme inactivo (ver [ADR-0007](adr/0007-render-cold-start-consciente.md)) |
| .NET 8 API | ✅ local | Render | 512 MB / mes | Web Service |
| Angular PWA | ✅ local | Netlify | Estático ilimitado | Publish `web/dist/preciolocal-web/browser` |
| PostgreSQL | ✅ local | Supabase | 500 MB, 2 projetos | Migraciones SQL |
| Redis | ✅ local (cache/lock/pubsub) | Upstash | Cached con quota | REST + WebSocket SDK |
| Gateway (Node 24) | ✅ local | Render | 512 MB / mes | GraphQL `/graphql` + `/health` |
| Java WebFlux | ✅ local | Railway | $5/mes × 12 | Alertas + STOMP `/ws`; se consumiría antes que Render |
| MongoDB | ✅ local (alertas) | Atlas M0 | Estadounense servidor, 512 MB | Cluster compartido 1.0.0 — ver [ADR-0004](adr/0004-mongodb-como-timeseries.md) |
| Observabilidad | 🟡 parcial | Grafana Cloud | 50GB logs/traces | `/metrics` apagado por defecto; falta verificación |

> MongoDB **sí lo consume `analytics-java`** para alertas y eventos. El
> histórico de precios sigue en Postgres; no es una base time-series de
> analytics.

## 2. Prerrequisitos globales

1. Cuentas: GitHub, Render, Railway, Netlify, Supabase, MongoDB Atlas, Upstash, Grafana Cloud.
2. Repo público en GitHub (`USER/preciolocal-er`).
3. Documentar secretos en:
   - **GitHub**: Actions secrets (para CI/CD y webhooks).
   - **Render/Railway/Netlify**: env vars por servicio.

## 3. Base de datos (Supabase + Atlas + Upstash)

### Supabase
1. Crear proyecto `preciolocal-er`.
2. Copiar `SUPABASE_URL`, `SUPABASE_DB_URL` (connection string), `SUPABASE_JWT_SECRET`.
3. Aplicar migraciones:
   - `psql "$SUPABASE_DB_URL" -f db/supabase/migrations/001_init.sql`
   - (repetir por cada `NNN_*.sql` en orden) — Fase 1.
4. Habilitar Auth (email magic link + Google).

### MongoDB Atlas
1. Cluster M0, usuario de DB + acceso desde cualquier IP (demo).
2. `MONGO_ATLAS_URL` en el servicio Java (no en el frontend).

### Upstash
1. Redis serverless: deshabilitar `restAPI` persistence; usar TLS.
2. `UPSTASH_REDIS_URL` + `UPSTASH_REDIS_TOKEN`.

## 4. Render (scraper, .NET, gateway)

Por cada Web Service:
1. `New Web Service` → conectar repo → rama `main`.
2. **Build**: Dockerfile del servicio (`services/<svc>/Dockerfile`).
3. **Start**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (scraper), `dotnet PrecioLocal.Api.dll` (.NET), `node dist/index.js` (gateway).
4. **Env vars** específicas del servicio.
5. **Health check path**: `/health`.
6. Desactivar "Auto deploy" a favor de webhook de deploy (en Fase 7).

## 5. Railway (Java)

1. `New Project` → connect repo → use `services/analytics-java/Dockerfile`.
2. Env vars: `DATABASE_URL`, `MONGO_ATLAS_URL`, `UPSTASH_REDIS_URL`, `UPSTASH_REDIS_TOKEN`.
3. Railway expone un puerto HTTP; WebSocket compatible a través de su proxy (definir health check `/health`).

> Free tier de Railway: 500MB + $5/mes por 12 meses (aplica proyecto) — revisar políticas vigentes.

## 6. Netlify (Angular)

1. Build settings:
   - Base directory: `web/`
   - Build command: `npm ci && npm run build`
   - Publish directory: `web/dist/preciolocal-web/browser`
   - Env: `GATEWAY_URL` (WS + HTTP), `SUPABASE_URL`, `SUPABASE_ANON_KEY`.
2. Habilitar SPA redirect (`/` → `index.html`).
3. PWA: build immutably + `Cache-Control` de assets.

## 7. CI/CD (GitHub Actions)

Estado real a 2026-10-20:

| Workflow | Trigger | Qué hace hoy | Qué promete el doc |
|---|---|---|---|
| `ci.yml` | PR, push a `master`/`main` | **8 jobs**: `validate-infrastructure`, `scraper-lint`, `scraper-test`, `web-test`, `gateway-test`, `web-e2e`, `dotnet-test`, `analytics-java-test` | — |
| `security.yml` | push + semanal (domingo 00:00) | `gitleaks` + `dependency-review` + Trivy + Dependabot | — |
| `deploy.yml` | — | **placeholder**: los pasos son comentarios, el smoke test es un `echo` y solo reenvía webhooks si existen los secrets | build → GHCR → webhook → smoke: **nunca se ejecutó** |

Detalle en `.github/workflows/`. El repo sigue **sin commits ni remoto**, así
que ningún workflow corrió todavía, aunque el trigger ya cubre `master` y
`main`.

## 8. Manteniendo vivo el free tier

- Render duerme servicios sin tráfico → **UptimeRobot** (free, 50 checks) ping `/health` cada 5 min.
- Cold start ~30–60 s frustra la demo → decisión consciente y UX compensatoria en [ADR-0007](adr/0007-render-cold-start-consciente.md).

## 9. Smoke test post-deploy

```bash
curl -f https://SCRAPER.onrender.com/health
curl -f https://DOTNET.onrender.com/health
curl -f https://JAVA.up.railway.app/health
curl -f https://GATEWAY.onrender.com/health
curl -f -o /dev/null -w "%{http_code}" https://preciolocal-er.netlify.app
```

Solo los dos primeros existen hoy. Localmente, el equivalente es:

```bash
docker compose up -d
docker compose ps                     # 8/8 healthy, todo en 127.0.0.1
curl -fsS http://127.0.0.1:8000/ready
curl -fsS http://127.0.0.1:4200/chains # por el proxy de nginx
```

## 10. Estado actual

**Estado real a 2026-10-21:** los 8 servicios del compose corren localmente
8/8 healthy (postgres, redis, mongo, scraper, ml-dotnet, gateway, analytics-java,
web). El deploy a free tiers sigue sin empezar: no hay cuentas ni secretos, y
`deploy.yml` es placeholder. Las filas de §1 son el plan de ejecución, no
infraestructura publicada.