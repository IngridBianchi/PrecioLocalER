# ADR-0008: No microfrontends, no K8s, no serverless

## Estado

Aceptado · 2026-09-23

## Contexto

La tentación en un portfolio es "sumar tecnologías que quedan bien": microfrontends con Module Federation, Kubernetes, AWS Lambda, event sourcing con outbox... Cada una de esas es una decisión legítima SOLO cuando hay un problema que la justifique. Acá los problemas reales son: 4 bounded contexts con repos propios, ~1 desarrollador, tráfico de decenas de usuarios, y free tier de 512MB por servicio.

## Decisión

**Ciudad explícita de no sobreingeniería:**

1. **No microfrontends.** Un solo Angular SPA. La división en *features* (dashboard, search, comparador...) es estructural, no de despliegue. Si el día 1 de producción hay 3 equipos de frontend, se evalúa.
2. **No Kubernetes.** Un compose y Dockerfiles multi-stage. La orquestación de 4 contenedores en un solo nodo no justifica un plano de control. Render/Railway ya managen el scheduling.
3. **No serverless por función.** El scraper es un worker de larga vida (locks, scheduler APScheduler); modelos de ML con warm-up. Serverless penaliza. Los servicios son Web Services clásicos.
4. **No event sourcing/outbox.** Los eventos se publican por pub/sub (ADR-0005) y el estado durable vive en Postgres. El outbox agrega infraestructura sin necesidad: la fuente de verdad ya es la DB transaccional.

**Regla de entrada a una tecnología:** "¿qué problema en producción la resuelve y qué cuesta activarla?" Si la respuesta exige 3 frases, no entra.

## Alternativas consideradas

| Tecnología | Cuándo SÍ volvería a considerarla |
|---|---|
| Microfrontends | 3+ equipos de frontend independientes |
| K8s | 10+ servicios / HA real / multi-cluster |
| Serverless por función | Carga de disparos cortos a escala |
| Apache Kafka | Necesidad de replay de eventos / durable log |

## Consecuencias

- **Positivas:** el repo se entiende en una sesión; CI simple; free tiers alcanzan; cada decisión "grande" se explica con su trigger de re-evaluación.
- **Negativas:** no demostraré K8s en este repo (la explicación de POR QUÉ no está es el valor).
- **Neutras:** documenta el criterio de decisión, que es lo que un entrevistador senior escucha.

## Referencias

- [ADR-0002-graphql-vs-rest](0002-graphql-vs-rest.md)
- [docs/architecture.md](../architecture.md)