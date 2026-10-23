# ADR-0005: Redis: cache + lock + pub/sub

## Estado

Aceptado · 2026-09-23

## Contexto

Hay tres problemas distribuidos reales que resolver: (1) el dashboard consulta el último precio de N productos — no quiero golpear Postgres en cada render; (2) el scheduler de scraping es único en un servicio horizontal — no quiero dos scrapes simultáneos de la misma cadena; (3) los eventos de precio/alerta deben propagarse en vivo a Java y al gateway — hay que desacoplar productores de consumidores.

## Decisión

**Redis (Upstash)** como pieza única para tres primitivas, cada una con un caso concreto:

| Primitiva | Uso | Detalle |
|---|---|---|
| **Cache** | `pl:cache:{product_id}` → último precio | TTL 60s; evita golpear PG en lecturas calientes del board |
| **Lock NX** | `pl:lock:{chain}` | `SET NX PX 300000`; garantiza un solo scrape por cadena aunque haya réplicas del worker |
| **Pub/sub** | `pl:events:price` · `pl:events:alert` | Productor = scraper/gateway; consumidores = AlertEngine Java y subscriptions del gateway; fallback LISTEN/NOTIFY de PG |

Redis **no es fuente de verdad**: nada importante se persiste solo ahí (pérdidas tolerables). El estado durable sigue en Postgres.

## Alternativas consideradas

| Alternativa | Pros | Contras | Por qué no |
|---|---|---|---|
| Caché en Postgres/App | No hay engine extra | No tiene TTL/lock/pubsub nativos | Redis resuelve 3 en uno |
| Kafka / RabbitMQ | Colas duras, replay | Payload/RAM que paga el free tier; operativa cara | Pub/sub efímero alcanza (los eventos no requieren replay) |
| Global SQLite per-node lock | Sin red | No funciona entre réplicas de servicios distintos | No |

## Consecuencias

- **Positivas:** 3 problemas con una primitiva barata; subscripciones GraphQL y alertas WS se alimentan del mismo canal; el lock nivel servicio evita duplicados de scrape (y abuso del sitio raspo).
- **Negativas:** Upstash free tier tiene quota limitada → hay que cachear con mesura y expiración honesta; Redis efímero implica tolerar pequeñas pérdidas de eventos (cubiertas por el fallback NOTIFY).
- **Neutras:** el contrato de eventos vive en [docs/contracts.md](../contracts.md).

## Referencias

- [ADR-0003-webflux-para-alertas](0003-webflux-para-alertas.md)
- [ADR-0007-render-cold-start-consciente](0007-render-cold-start-consciente.md)