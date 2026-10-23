import { ApolloServer } from '@apollo/server';
import { ApolloServerPluginDrainHttpServer } from '@apollo/server/plugin/drainHttpServer';
import fastify from 'fastify';
import { WebSocketServer } from 'ws';
import { useServer } from 'graphql-ws/lib/use/ws';
import { createContext, Context } from './resolvers/index.js';
import { resolvers } from './resolvers/index.js';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { rateLimitPlugin, otelPlugin } from './plugins/index.js';
import { logger } from './utils/logger.js';
import { getConfig } from './utils/config.js';
import client from 'prom-client';

const config = getConfig();

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Load schema
const typeDefs = readFileSync(join(__dirname, 'schema', 'index.graphql'), 'utf-8');

// Create Fastify app
const app = fastify({
  logger: false, // We use our own pino logger
});

app.addHook('onRequest', async (request, reply) => {
  request.headers['x-correlation-id'] = request.headers['x-correlation-id'] || crypto.randomUUID();
});

// Health check endpoint (not GraphQL)
app.get('/health', async () => {
  return { status: 'ok', service: 'gateway', timestamp: new Date().toISOString() };
});

// Prometheus metrics endpoint (Fase 6)
client.collectDefaultMetrics();
app.get('/metrics', async (request, reply) => {
  reply.header('Content-Type', client.register.contentType);
  return client.register.metrics();
});

// GraphQL endpoint will be at /graphql

async function start() {
  // Create Apollo Server
  const server = new ApolloServer<Context>({
    typeDefs,
    resolvers,
    plugins: [
      ApolloServerPluginDrainHttpServer({ httpServer: app.server }),
      rateLimitPlugin(),
      otelPlugin(),
    ],
    introspection: config.NODE_ENV !== 'production',
    includeStacktraceInErrorResponses: config.NODE_ENV !== 'production',
  });

  await server.start();

  // Register Apollo with Fastify
  await app.register(async (fastify) => {
    fastify.route({
      method: ['POST', 'GET'],
      url: '/graphql',
      handler: async (request, reply) => {
        // Adapt Fastify request to RequestLike interface
        const requestLike = {
          headers: {
            get: (name: string) => request.headers[name.toLowerCase()] || null,
          },
          body: request.body as Record<string, unknown> | undefined,
        };
        const context = await createContext(requestLike);
        
        // Extract GraphQL request from Fastify request body
        const body = request.body as { query?: string; variables?: Record<string, unknown>; operationName?: string | null } | undefined;
        
        const response = await server.executeOperation(
          {
            query: body?.query || '',
            variables: body?.variables || {},
            operationName: body?.operationName || null,
          },
          {
            contextValue: context,
          }
        );

        // Apollo Server returns { body: { kind, singleResult: { data, errors } } }
        // Apollo Client expects { data, errors } at top level
        const graphqlResponse = response.body.kind === 'single'
          ? response.body.singleResult
          : { errors: [{ message: 'Unexpected response kind' }] };

        // Always return 200 for GraphQL responses (errors go in response body per GraphQL spec)
        reply.status(200);
        return graphqlResponse;
      },
    });
  });

  // WebSocket for GraphQL subscriptions (graphql-ws)
  const wsServer = new WebSocketServer({ server: app.server, path: '/graphql' }) as any;
  const serverCleanup = useServer(
    {
      schema: (server as any).schema,
      context: async (ctx) => {
        const connectionParams = (ctx.connectionParams || {}) as Record<string, unknown>;
        const requestLike = {
          headers: {
            get: (name: string) => (connectionParams[name.toLowerCase()] as string) || null,
          },
        };
        return createContext(requestLike);
      },
    },
    wsServer
  );

  // Graceful shutdown for ws server
  app.addHook('onClose', async () => {
    await serverCleanup.dispose();
    wsServer.close();
  });

  const port = config.PORT;
  try {
    await app.listen({ port, host: '0.0.0.0' });
    logger.info({ port }, `🚀 Gateway ready at http://localhost:${port}/graphql`);
  } catch (err) {
    logger.error({ err }, 'Failed to start server');
    process.exit(1);
  }
}

// Graceful shutdown
process.on('SIGTERM', async () => {
  logger.info('SIGTERM received, shutting down gracefully');
  await app.close();
  process.exit(0);
});

process.on('SIGINT', async () => {
  logger.info('SIGINT received, shutting down gracefully');
  await app.close();
  process.exit(0);
});

start();