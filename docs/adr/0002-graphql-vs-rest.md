# ADR-0002: GraphQL vs REST directo

## Estado

Aceptado · 2026-09-23

## Contexto

La web necesita: búsqueda con filtros, comparación multi-producto (que dispara N consultas de precios), histórico, predicciones, reportes y alertas en tiempo real. Con REST tendríamos un cliente armando respuestas desde varios endpoints, con over/under-fetching y N+1 peor: el comparador de 10 productos = consultas anidadas al histórico.

## Decisión

Un **gateway Apollo Server 4** como única superficie GraphQL para el frontend, que internamente orquesta los 3 backends REST con **DataLoader** para batching (anti N+1) y **graphql-ws** para subscriptions en tiempo real.

- Las queries modelan exactamente lo que la UI necesita (schema-first, contract primero).
- Mutations para acciones de usuario (`createAlert`) y admin (`runScrape`).
- Subscriptions `onPriceUpdate` / `onAlertTriggered` alimentadas por Redis pub/sub + WebSocket.

## Alternativas consideradas

| Alternativa | Pros | Contras | Por qué no |
|---|---|---|---|
| REST directo por servicio | Simple, sin capa extra | Cliente arma respuestas; N+1 del comparador; sin subscriptions nativas | No escala para comparador + tiempo real |
| GraphQL en cada micro | Cada servicio se autodescribe | 3 schemas que mantener, federación innecesaria sin muchos equipos | Overkill para el tamaño |
| Apollo Federation | Composición declarativa | Requiere subgraphs y más infra de Apollo | No justificada a esta escala (ver ADR-0008) |

## Consecuencias

- **Positivas:** una fuente de verdad para el cliente; el N+1 se resuelve en DataLoader, medible con tracing; las subscriptions son de primera clase y demuestran GraphQL avanzado.
- **Negativas:** latencia de orquestación (mitigada con DataLoader + caché Redis en micros); agregar un servicio requiere tocAR el gateway.
- **Neutras:** los micros siguen siendo REST (contratos simples, fácil de testear) y el valor de GraphQL queda en la capa correcta.

## Referencias

- [docs/architecture.md](../architecture.md)
- [ADR-0008-no-microfrontends-no-k8s](0008-no-microfrontends-no-k8s.md) — por qué no federación exuberante