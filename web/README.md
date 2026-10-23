# web — Angular 20 + PWA

Frontend PWA que consume el scraper Python por REST y el gateway Apollo por GraphQL a través del proxy de nginx. Incluye modo oscuro, paginación server-side y estados de carga/vacío/error en las pantallas principales.

**Features implementadas:** búsqueda, detalle de producto, dashboard, comparador, alertas y predicciones.

**Stack:** `@angular/core` (standalone + signals) · `@angular/router` · `@angular/forms` · `@angular/service-worker` (PWA) · `apollo-angular` · `@apollo/client` · `graphql` · `graphql-ws` · `chart.js` · `rxjs` · Karma + Jasmine · Playwright (E2E).

## Estructura

```
src/app/
  core/          # apollo.config, auth, graphql-api, notifications
  shared/        # models, scraper-api, forecast-api, chain-picker
  features/
    search/
    product-detail/
    dashboard/
    comparator/
    alerts/
    predictions/
```

## Comandos

```bash
npm ci
npm start                                    # ng serve (dev)
npx ng test --watch=false --browsers=ChromeHeadless   # 35 tests
npm run build                                # build de producción PWA

# E2E (stack del compose levantada)
npm run e2e:install
npm run e2e
```

## Cómo llega al backend

El browser llama a `environment.apiBaseUrl` y `environment.mlDotnetBaseUrl`, que en el build de contenedor apuntan al proxy de nginx en `:4200`. Ese proxy reparte por prefijo:

| Prefijo | Va a |
|---|---|
| `/health`, `/ready`, `/chains`, `/products*`, `/scrape*`, `/ml*` | scraper Python `:8000` |
| `/api*` | API .NET `:8001` |
| `/graphql`, `/ws` | gateway Node `:4000` |
| todo lo demás | `index.html` de Angular (SPA) |

Si se agrega un endpoint nuevo al backend, hay que agregarlo también al regex del `location` en `web/docker/nginx.conf`; si no, el browser recibe el `index.html` en vez de JSON. `e2e/proxy-contrato.spec.ts` fija ese contrato.

## Estado

Frontend completo a nivel de features (6/6). PWA con iconos pendientes y Lighthouse no medido. Modo oscuro persistido en `localStorage`.
