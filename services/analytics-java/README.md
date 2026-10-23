# services/analytics-java — Java 17 + Spring Boot 3 WebFlux

Motor de alertas reactivo + WebSocket STOMP.

**Rol único (ver [ADR-0003](../../docs/adr/0003-webflux-para-alertas.md) y [ADR-0005](../../docs/adr/0005-redis-cache-lock-pubsub.md)):** consume eventos de precios, evalúa umbrales de alertas, persiste `alert_events` y hace push en tiempo real. NO entrena modelos ni persiste precios crudos.

## Stack

`spring-boot-starter-webflux` · R2DBC (Postgres) · Data MongoDB Reactive · spring-boot-starter-websocket · Resilience4j · micrometer-tracing-otel

## Endpoints

```text
GET /health
GET /ready                              Postgres + Mongo
GET /api/alert-events?userId=           historial (rate limited)
WS  /ws                                 STOMP: /topic/alerts, /topic/prices
```

El motor consume `pl:events:price` (Redis pub/sub) y publica en `pl:events:alert`. El scraper todavía no publica `pl:events:price`; hoy el smoke inyecta el evento a mano.

## Estructura real

```text
src/main/java/.../config
src/main/java/.../controller
src/main/java/.../model
src/main/java/.../repository
src/main/java/.../service
src/test/java/...
```

## Tests

JUnit 5 + Reactor Test (`StepVerifier`) + Testcontainers. Estado actual: 14/14 (6 unit + 8 integration).

## Estado

Implementado y operativo en local (imagen Docker construida, servicio healthy, `/ready` con Postgres + Mongo). Deploy público no iniciado.
