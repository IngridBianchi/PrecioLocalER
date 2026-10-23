# ADR-0003: WebFlux para el motor de alertas

## Estado

Aceptado · 2026-09-23

## Contexto

El motor de alertas es el corazón "vivo": los precios entran como un stream continuo (cada scrape genera decenas de `price_point` por cadena), se deben evaluar contra todas las alertas activas, persistir `alert_events` y emitir por WebSocket a los usuarios — todo sin bloquear el hilo de I/O y con la capacidad de **aplicar contrapresión** si una cola se atrasa.

## Decisión

**Java 17 + Spring Boot 3 WebFlux** (proyecto reactivo) para el `AlertEngine`:

- R2DBC para PostgreSQL y **Data MongoDB Reactive** para analytics: todo el camino de datos es reactivo (no hay bloqueos de hilo de la DB hasta el socket).
- Consumo de eventos: Redis pub/sub + respaldo `LISTEN/NOTIFY` de Postgres.
- Backpressure real: `Flux` con límites configurables; si el productor corre más que el procesamiento, aplicamos buffer/ventana y logging de throttling (señal de operación, no solo "async" decorativo).
- WebSocket STOMP para `/topic/alerts` y `/topic/prices` como borde de salida.

### Por qué Reactive y no MVC + executor

MVC con un pool de hilos funcionaría para picos pequeños; con un stream continuo de eventos y dos DBs reactivas por medio, cada hilo bloqueado es cola. El problema es backpressure y concurrencia sin hilos bloqueados → dominios reactivos.

## Alternativas consideradas

| Alternativa | Pros | Contras | Por qué no |
|---|---|---|---|
| Spring MVC + JPA | Familiar, maduro | Hilos bloqueados en DB y socket; backpressure manual | No demuestra el dominio |
| Peer de Java: Kotlin coroutines | Moderno | Menos bibliografía empresarial | Spring WebFlux + Reactor es el estándar a defender |
| Node (mismo runtime del gateway) | Un solo lenguaje | Ya Node orquesta; tres Runtime suelto menos (ver ADR-0001) | El ADR-0001 separa bounded contexts |

## Consecuencias

- **Positivas:** el motor maneja picos reales sin threads bloqueados; `StepVerifier` permite probar cada flujo determinísticamente (test reactivo, no mocks de hilos).
- **Negativas:** curva de aprendizaje (Reactor, R2DBC); el debugging de streams reactivos es más labioso que el imperativo.
- **Neutras:** testcontainers acoplan el ciclo de tests a Docker.

## Referencias

- [ADR-0005-redis-cache-lock-pubsub](0005-redis-cache-lock-pubsub.md)
- [docs/contracts.md](../contracts.md) — eventos `alerts` / `alert_events`