# Seguridad — PrecioLocal ER (OWASP Top 10)

Checklist vivo: cada mitigación se implementa en alguna fase y se marca su estado. Objetivo: poder mostrar a un entrevistador **qué** se hizo, **dónde** y **cómo**.

## 1. Checklist OWASP Top 10 (2021)

Estado real al 2026-10-20. La columna "Estado" distingue lo verificado de lo
planificado. El gateway ya verifica JWT y el motor Java ya corre; lo que sigue
parcial es el login real en la UI y la propagación del usuario a la RLS.

| # | Riesgo | Mitigación implementada | Servicio | Estado |
|---|---|---|---|---|
| A01 | Broken Access Control | RLS en Postgres con `pg_has_role` (membresía heredada, no `current_user`); roles de servicio `NOLOGIN` sin `BYPASSRLS`; grupos separados `pl_scraper`/`pl_app`/`pl_ml`/`pl_analytics`; `X-Admin-Token` auditado sin loguear secreto; advisory lock por cadena | DB, scraper, analytics | ✅ (P0.1, P0.3, 012) |
| A02 | Cryptographic Failures | Secrets solo en env vars (`.env` gitignored, `.env.example` sin valores); `gitleaks` en CI; JWT RS256 (JWKS) + HS256 fallback | todos | 🟡 parcial — falta HTTPS en local y Supabase Auth |
| A03 | Injection | Pydantic + queries parametrizadas (asyncpg, EF Core, R2DBC); sin concatenación SQL | scraper, .NET, Java | ✅ |
| A04 | Insecure Design | Decisiones en [ADRs](adr/); rate limiting por servicio (ver §3) | todos | ✅ |
| A05 | Security Misconfiguration | Headers seguros en nginx (§5); CORS por ambiente; imágenes multi-stage; contenedores sin root; puertos en `127.0.0.1`; `x-required-env` en compose; `007_rls_revoke.sql`; `011_service_roles.sql` | Docker, DB | ✅ |
| A06 | Vulnerable Components | `gitleaks` + `dependency-review` + **Trivy (fs + image)** + **Dependabot (pip, npm, docker, nuget)** en `security.yml`; `requirements.lock` fijado; `pom.xml` fijado; `package.json` fijado | CI | ✅ |
| A07 | Authentication Failures | Supabase Auth (magic link / OAuth) + verificación JWT en gateway (`jose` + JWKS/HS256); Angular tiene `AuthService` y agrega el Bearer al link de Apollo; falta login real y propagación del usuario a la RLS | Gateway, web | 🟡 parcial (2026-10-20) |
| A08 | Data Integrity | Hash SHA256 único en `price_points` (dedupe + idempotencia) que **incluye `sku_id`**; `raw_json` validado; fuente de verdad única (Postgres) | scraper, DB | ✅ |
| A09 | Logging Failures | Logs JSON estructurados con `structlog` (Python), `pino` (Node), `logback` (Java), `Serilog` (.NET); **`correlation_id` propagado end-to-end** via headers `X-Correlation-Id` + MDC | todos | ✅ (Fase 6) |
| A10 | SSRF | URLs de scraping **hardcoded por adaptador**; `follow_redirects=False`; timeouts con retry limitado; circuit breaker | scraper | ✅ |

> **Nota nginx (A5):** `web/Dockerfile` corre como root a propósito. El
> proceso master de nginx necesita eluid 0 para bindear `:80`; el dropping real
> ocurre en el `user` directive de `nginx.conf`, que hoy **no está** porque el
> alpine oficial ya corre los workers como `nginx`. Es el punto flojo conocido de
> esta fila.

## 2. Modelo de autenticación

🟡 **Parcial: el gateway verifica el JWT; el login de la UI y la propagación a la
RLS todavía no están cerrados.** El flujo que se busca:

```
1. Angular → Supabase Auth (email magic link o Google)
2. Supabase devuelve JWT (access + refresh)
3. Angular envía el JWT como Bearer
4. Gateway verifica firma con `jose` + JWKS de Supabase (sin llamada de red)
5. Gateway propaga X-User-Id y X-Correlation-Id a los micros
6. Los micros confían en estos headers internos (red privada / token interno)
```

**Lo que existe hoy:** `AuthService` de Angular crea el cliente Supabase y
adjunta el token al link de Apollo; el gateway valida RS256 con JWKS (o HS256
con `SUPABASE_JWT_SECRET` como fallback de desarrollo). Las queries que exponen
datos de usuario exigen `context.user`. Lo que falta: configurar Supabase real,
pantalla de login y setear `app.user_id` en la conexión a Postgres. Sigue
existiendo `X-Admin-Token` para `POST /scrape/run/{chain}`, fuera del proxy
público (§3.2).

### 2.1 Aislamiento a nivel de fila (RLS) — sí implementado

Esto **sí** está en la base, en `007_rls_revoke.sql`, y no depende del gateway:

- RLS activa en las tablas con datos de usuario (`users`, `alerts`,
  `alert_events`) y en las públicas (`price_points`, `predictions`: lectura
  abierta, escritura solo del rol de servicio).
- El aislamiento por usuario usa el GUC `app.user_id`, que la capa de conexión
  setea al resolver el JWT: `SET LOCAL app.user_id = '<uuid>'` dentro de la
  transacción.
- Si el GUC **no** está seteado, `pl_current_user_id()` devuelve `NULL` y la
  política deniega: es deny-by-default, no hay fuga entre usuarios.
- `REVOKE` de `EXECUTE` sobre `pg_notify`; el trigger de `003` pasa a
  `SECURITY DEFINER` para seguir notificando sin privileges de la app.
- Roles de servicio `NOLOGIN` (sin contraseña, sin login directo).

Ojo: la parte de **RLS** está completa, pero la de **setear el GUC desde el
gateway/Java** todavía no. El motor de alertas usa un rol de servicio con
policies propias (migración 012), no `app.user_id`; el deny-by-default de las
tablas de usuario sigue siendo la protección por defecto.

### 2.2 Roles de servicio y separación de privilegios

| Rol LOGIN | Grupo | Uso | Grants principales |
|---|---|---|---|
| `preciolocal_scraper` | `pl_scraper` | scraper Python | productos, SKUs, precios; sin alertas |
| `preciolocal_ml` | `pl_ml` | ml-dotnet | lectura de precios e históricos; sin alertas |
| `preciolocal_analytics` | `pl_analytics` | analytics-java | `SELECT/UPDATE` en `alerts`; `SELECT/INSERT` en `alert_events`; sin productos |

`scripts/db-service-roles.sh` crea los LOGIN con
`LOGIN INHERIT NOSUPERUSER NOCREATEROLE NOBYPASSRLS`, les asigna **un solo**
grupo y revoca cualquier membresía anterior. Los grupos `NOLOGIN` los crean las
migraciones `011` y `012`. Esto evita que analytics herede `pl_ml` y pueda leer
precios, o que ml-dotnet pueda tocar alertas de usuarios.

## 3. Rate limiting

| Servicio | Herramienta | Límite | Estado |
|---|---|---|---|
| Python scraper | `slowapi` (storage Redis si hay `REDIS_URL`, si no memoria) | 30 req/min por IP; `POST /scrape/run` 5/h por usuario, 1/h por chain | ✅ Fase 1 |
| .NET | `Microsoft.AspNetCore.RateLimiting` (rate limiter **integrado**, ventana fija particionada por IP remota) — política `ml`, aplicada a los 4 endpoints de forecast/reports | 60 req/min por IP | ✅ Fase 2 |
| Gateway | `graphql-rate-limit-directive` | 100 req/min por IP | ✅ Fase 4 |
| Java | Resilience4j `RateLimiter` | 60 req/min por IP | ✅ Fase 3 |
| Scraper → cadenas | delays configurados + retries + circuit breaker + `robots.txt` | conservador | ✅ Fase 1 |

El límite de .NET vive en `Program.cs:109` (`AddRateLimiter`), no en un
paquete externo: `AspNetCoreRateLimit` no está instalado.

### 3.1 Qué es "por IP" (y por qué el header no alcanza)

Un rate limit por IP solo sirve si la IP que se usa para partir es la del
cliente. Eso en un stack con reverse proxy no es automático, y era exactamente
el agujero: los dos servicios leían mal la identidad del cliente, así que el
límite era o decorativo (bucket por IP inventada) o global (un bucket para
todo el mundo).

Hay tres lugares donde se decide, y los tres tienen que coincidir:

| Dónde | Qué hace | Configurado en |
|---|---|---|
| nginx | **sobreescribe** `X-Forwarded-For` con `$remote_addr` (no `$proxy_add_x_forwarded_for`, que hace append de lo que le manden) | `web/docker/nginx.conf` |
| uvicorn | aplica el XFF solo si el peer está en `--forwarded-allow-ips` | `services/scraper/Dockerfile` ← `TRUSTED_PROXY_IP` |
| Kestrel | idem, vía `UseForwardedHeaders` + `KnownProxies` | `services/ml-dotnet/Program.cs` ← `TRUSTED_PROXY_IP` |

`TRUSTED_PROXY_IP` (default `10.99.0.10`) es la IP **fija de nginx**, y la
subred `INTERNAL_SUBNET` (default `10.99.0.0/24`) está fijada en el compose
para que esa IP exista. Es una sola IP y no la subred: el gateway de Docker
vive en la subred y tampoco debe poder firmar un XFF.

Del lado del scraper, `client_ip()` ya **no** parsea el header: usa
`request.client.host`, que es la IP que el servidor resolvió. Reimplementar
esa decisión en la app era el agujero que quedaba abierto.

Verificado a mano (no solo por config): con el límite bajado a 3/min, seis
requests directos al puerto 8000 con `X-Forwarded-For` distinto cada uno dan
200/200/200/429/429/429 — antes de este cambio las seis eran 200. Y con dos
clientes de IP distinta (el host y un contenedor), cada uno tiene su propia
cuenta en ambos servicios, o sea que no quedaron en un bucket global.

Guardas automatizadas: `web/e2e/proxy-contrato.spec.ts` (que nginx
sobreescriba el XFF, que la allow-list sea una sola IP, que los tres puntos
coincidan y que `POST /scrape/run` responda 403 en el dominio público) y
`services/scraper/tests/integration/test_api.py::test_client_ip_ignora_x_forwarded_for_forjado`.

### 3.2 Superficie de escritura fuera del proxy público

`POST /scrape/run/{chain}` dispara scraping contra sitios externos y
`GET /scrape` lista el estado. **No se sirven por el dominio público**: nginx
los corta con `deny all` (403), con o sin `X-Admin-Token` válido.

Se eligió un 403 explícito y no simplemente sacarlos del regex del API: si
`/scrape` dejara de matchear, caería en la SPA y devolvería `index.html` con
200 — un fallo silencioso que parece un scrape exitoso. Para dispararlos se
usa el puerto local del scraper: `make scrape CHAIN=la_anonima` o
`curl -X POST localhost:8000/scrape/run/<chain> -H "X-Admin-Token: $ADMIN_TOKEN"`,
que sigue funcionando igual que antes.

Cuando exista el gateway (§2), esto pasa a ser una decisión de ruteo del gateway
y no de nginx.

### 3.3 Lock por cadena: un scrape por cadena, no uno por HTTP request

El rate limit de cadena (1 scrape por cadena y hora, §3) frena el abuso, pero
**no impide que dos peticiones que pasaron el bucket se solapen**. Los dos
`POST /scrape/run/{chain}` entran, los dos runners escriben a la vez sobre
`products` / `product_skus` / `price_points`, y el barrido de SKUs
desactivados de la migración 010 decide qué está "desaparecido" mirando el
resultado del scrape que el otro está todavía escribiendo. Con `ON CONFLICT` la
corrupción es improbable, no imposible.

Por eso el endpoint toma un **advisory lock de Postgres por cadena** antes de
correr (`app/api/router.py`, `_chain_lock`):

| Situación | Status | Evento de log |
|---|---|---|
| Lock tomado, scrape corre | 200 | `scrape.iniciado` → `scrape.terminado` |
| Lock tomado por otro | **409** `En curso` | `scrape.lock.ocupado` |
| Sin pool de DB | 503 | `scrape.error` (fail-closed) |

Tres decisiones que no son obvias:

1. **El lock es de sesión y la conexión se retiene** (`pool.acquire()` dentro
   del `async with`). `pg_try_advisory_lock` es de sesión: sobrevive a los
   commits intermedios del scrape y se libera solo si la conexión muere. Si se
   tomara y soltara por el pool directamente (una conexión distinta por query),
   el unlock no encontraría nada, devolvería `false` en silencio y la cadena
   quedaría trabada.
2. **La clave es `hashtext(slug)`, no un id de cadena.** Los ids de la base de
   test son los del seed y los de producción son otros; con el slug el lock es
   el mismo concepto en cualquier base y queda legible en `pg_locks`.
3. **Fail-closed.** Sin DB no hay lock, y sin lock no hay scrape (503), aunque
   hoy haya una sola réplica.

Con los defaults de hoy (1 scrape por cadena y hora) el 409 del lock es
**inalcanzable**: el bucket responde 429 antes. Es defensa en profundidad para
cuando se suban los límites, cuando las peticiones vengan de IPs distintas
(el bucket es por IP, el lock es por cadena), o cuando haya más de una réplica.
Los límites de scrape **no están en el `environment` de `docker-compose.yml`**,
así que el contenedor siempre corre con el default del código; para probarlos
hay que pasarlos por compose explícitamente.

### 3.4 Auditoría del token admin (y por qué no se loguea)

Todo intento de `POST /scrape/run/{chain}` queda registrado en el log
estructurado, con dos propiedades:

- **El valor del token nunca se loguea.** Se registra `token_present` (si vino
  header) y `provided_len` (cuántos caracteres), no el contenido. Con
  `token_present=true` y `provided_len=0` se distingue "mandó basura" de
  "mandó el correcto pero con un typo de una letra", que es la diferencia entre
  un ataque y un error de tipeo. El log es el lugar donde vive el secreto si
  uno lo escribe ahí, y el log se backup-ea y se indexa.
- **El fallo se audita antes de que el rate limit lo vea.** `require_admin` es
  una dependencia que corre antes que slowapi: un 401 no consume el bucket por
  usuario ni el de cadena. Un atacante no puede agotar el cupo del admin con
  peticiones que van a fallar igual.

La validación sigue siendo `secrets.compare_digest` (tiempo constante).

## 4. Secretos

- Nunca en git (`.gitignore` + `gitleaks` en CI).
- Vars de entorno por ambiente (`.env.example` documenta todos).
- En producción: Render/Railway/Netlify env vars + GitHub Secrets.
- Rotación documentada en [deploy.md](deploy.md).

## 5. Headers de seguridad (respuestas HTTP)

Implementado en `web/docker/nginx.conf` (server + locations con caché, para no heredar parcialmente):

- `X-Content-Type-Options: nosniff` + `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy` (geolocation/mic/camera off)
- `Content-Security-Policy` (default-src 'self'; style-src con 'unsafe-inline' para chart.js; frame-ancestors 'none'; sin inline scripts)
- `Strict-Transport-Security` (aplica al llegar HTTPS en deploy; en local HTTP el browser lo ignora)
- CORS **restringido por ambiente** (`CORS_ORIGINS`, default `http://localhost:4200`)

## 6. Threat model resumido

| Activo | Amenaza principal | Control principal |
|---|---|---|
| API keys / JWT | Robo de token | Expiración corta, HTTPS, rotación |
| Base de precios | Inserción de datos falsos | Hash idempotente + validación + source fijo |
| Plasma de scraping | Uso abusivo / SSRF | URLs fijas, delays, rate limit, captcha→skip |
| WebSocket | Eavesdropping / DoS | WSS obligatorio, autenticación en handshake, rate limit de frames |

## 7. Live status

Última revisión: **2026-10-20**, con la stack local 8/8 healthy en loopback
(incluido `analytics-java`).

Verificado y funcionando:

- **A01** RLS real con roles `NOLOGIN` sin `BYPASSRLS`, policies con `pg_has_role`, auditoría de `X-Admin-Token`, advisory lock por cadena (P0.1, P0.3).
- **A03** queries parametrizadas (asyncpg, EF Core, R2DBC), sin concatenación SQL.
- **A05** headers de seguridad en nginx (§5), CORS por ambiente, passwords obligatorias en compose (`x-required-env`), contenedores sin root, puertos en `127.0.0.1`, RLS hardening (`007_rls_revoke.sql`, `011_service_roles.sql`, `012_analytics_service_role.sql`).
- **A06** Trivy (fs + image scan) + Dependabot (pip, npm, docker, nuget) en `security.yml` (semanal).
- **A08** hash idempotente con `sku_id` incluido.
- **A09** Logs JSON con `correlation_id` propagado end-to-end (Python `structlog`, Node `pino`, Java `logback`+MDC, .NET `Serilog`).
- **A10** sin redirects, URLs fijas por adaptador, `robots.txt` con fail-safe, circuit breaker.
- **Rate limiting** en scraper (slowapi), .NET (60/min), Java (Resilience4j 60/min), Gateway (100/min).
- **Secretos**: `.env` gitignored, `.env.example` sin valores, `gitleaks` en CI, rotación con `db-rotate-roles.sh`.

Pendiente, en orden de importancia:

1. **A07** Supabase Auth + JWT verification en gateway: falta el login real de la UI y propagar el usuario a `app.user_id`; el motor de alertas usa rol de servicio (`pl_analytics`).
2. **A02** HTTPS en local sin resolver (HTTP); deploy free tiers proveen TLS.
3. **A05** contenedor web corre como root (nginx master en puerto 80).

Correo de transparencia: UA configurable (default navegador) porque cloudfront
de La Anónima responde 403 a UAs de bots; se mantienen robots.txt, delays y volumen bajo.