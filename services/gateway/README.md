# services/gateway — Node 24 + Apollo Server 4 (TypeScript)

Única superficie GraphQL pública; orquesta los backends (scraper, ml-dotnet, analytics-java) por REST con DataLoader y subscriptions por WebSocket.

**Rol único (ver [ADR-0002](../../docs/adr/0002-graphql-vs-rest.md)):** schema-first, resolvers, DataLoader anti-N+1, auth JWT (Supabase/JWKS via `jose`), rate limit, subscriptions. NO tiene lógica de negocio.

## Stack

`@apollo/server` · `graphql-ws` · `ws` · `dataloader` · `graphql-rate-limit-directive` · `jose` · `pino` · OpenTelemetry

## Schema (resumen)

```graphql
type Query {
  searchProducts(q: String!, chain: String, limit: Int, offset: Int): ProductSearchPage!
  product(id: ID!): Product
  chains: [Chain!]!
  priceHistory(id: ID!, span: String!, chain: String): [PricePoint!]!
  getForecast(productId: ID!, chain: String, referencePrice: Float): CombinedForecast
  getInflationReport(span: String!): InflationReport
  getTopMovers(span: String!, limit: Int): TopMovers
}
type Mutation {
  createAlert(input: AlertInput!): Alert!
  toggleAlert(id: ID!, active: Boolean!): Alert!
  deleteAlert(id: ID!): Boolean!
}
type Subscription {
  onPriceUpdate(productId: ID!): PricePoint!
  onAlertTriggered(userId: ID!): AlertEvent!
}
```

Los reportes de inflación/top movers ahora se calculan en ml-dotnet y el gateway los expone. Las subscriptions (`onPriceUpdate`, `onAlertTriggered`) siguen en stub.

## Estructura real

```
src/
  index.ts
  schema/
  datasources/
  resolvers/
  plugins/
tests/
  unit.test.ts
  integration.test.ts
```

## Estado

Implementado y operativo en local (9/9 tests, `tsc` verde, `/graphql` responde). Apollo Server 4 alcanzó EOL el 2026-01-26; migración a Apollo Server 5 pendiente. Deploy público no iniciado.
