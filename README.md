# PrecioLocal ER

Comparador de precios de supermercados de Entre Ríos con predicción de tendencias y alertas en tiempo real. Raspa sitios públicos de retail, normaliza precios por SKU, los compara entre cadenas y expone una API GraphQL detrás de un frontend Angular con PWA.

## Stack

- **Scraper + ML:** Python 3.12 + FastAPI (Holt-Winters y robust z-score en producción; SARIMAX/IsolationForest con el extra `.[ml]`)
- **Reportes y forecast:** .NET 8 + ML.NET (SsaForecaster)
- **Alertas en tiempo real:** Java 17 + Spring Boot WebFlux (WebSocket STOMP, Redis pub/sub)
- **API unificada:** Node 24 + Apollo Server 4 (GraphQL, DataLoader, JWT, rate limit)
- **Frontend:** Angular 20 + Apollo Angular (PWA)
- **Bases de datos:** PostgreSQL 16 (transaccional), MongoDB 7 (analytics), Redis 7 (cache/lock/pub-sub)

## Cómo levantarlo (local)

Requisitos: Docker (compose v2), Git. Opcional: Node 22+ para desarrollo del frontend.

```bash
git clone https://github.com/vparr/preciolocal-er.git
cd preciolocal-er
cp .env.example .env   # editar si hace falta (ADMIN_TOKEN para scrape manual)
docker compose up -d   # postgres + redis + mongo + scraper + ml-dotnet + analytics-java + gateway + web
```

Puertos (loopback):

- Web (Angular + nginx): http://localhost:4200
- Scraper (REST, docs en `/docs`): http://localhost:8000
- ml-dotnet: http://localhost:8001
- Gateway (GraphQL): http://localhost:4000
- analytics-java: http://localhost:8080

Cargar datos reales:

```bash
curl -X POST localhost:8000/scrape/run/la_anonima  -H "X-Admin-Token: $ADMIN_TOKEN"
curl -X POST localhost:8000/scrape/run/coto_digital -H "X-Admin-Token: $ADMIN_TOKEN"
curl -X POST localhost:8000/scrape/run/carrefour    -H "X-Admin-Token: $ADMIN_TOKEN"
curl -X POST localhost:8000/scrape/run/changomas    -H "X-Admin-Token: $ADMIN_TOKEN"
```

Luego abrir http://localhost:4200 y buscar "aceite".

Desarrollo web sin Docker:

```bash
cd web
npm install
npm start   # http://localhost:4200
```

## Notas

- `cooperativa_obrera` está en la tabla `chains` pero no tiene scraper: el sitio es institucional (Joomla, sin catálogo público) y su portal de clientes es login de cuentas corrientes. Ver `CONTEXTO.md` §16.
- Redis es opcional: sin `REDIS_URL`, `/ready` solo chequea la DB y el rate limit usa memoria.
- El reporte de inflación/top movers del gateway y el CRUD de alertas siguen en stub; el motor de alertas se prueba publicando un evento manual en Redis.

## Testing

```bash
# Python (WSL2, venv ~/.venvs/pl)
~/.venvs/pl/bin/python -m pytest -q --cov=app

# Angular
cd web && npx ng test --watch=false --browsers=ChromeHeadless

# E2E (stack arriba)
cd web && npx playwright test
```

Estado actual: scraper 368 tests (90 %), .NET 13 unit, Java 14 (6 unit + 8 integration), gateway 9 (4 unit + 5 integration), Angular 35 unit + 29 E2E. CI definida con 8 jobs pero nunca ejecutada (repo sin push).

## Documentación

- Arquitectura: `docs/architecture.md`
- Contratos: `docs/contracts.md`
- Seguridad: `docs/security.md`
- Testing: `docs/testing.md`
- Deploy: `docs/deploy.md`
- ADRs: `docs/adr/`

## Disclaimer legal (scraping)

Este proyecto raspa sitios públicos de retail con fines educativos y de investigación, respetando `robots.txt` de forma conservadora: volúmenes bajos, delays configurados y sin eludir medidas técnicas de protección. Los datos publicados son orientativos y no reemplazan los precios oficiales en góndola.

## Contacto

Autor: Víctor Parra — [LinkedIn](https://linkedin.com/in/vparr) · [GitHub](https://github.com/vparr) · vparr@example.com
