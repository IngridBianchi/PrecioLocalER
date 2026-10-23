import { ApolloServerPlugin, GraphQLRequestContext } from '@apollo/server';
import { BaseContext } from '@apollo/server';
import { getConfig } from '../utils/config.js';
import { logger } from '../utils/logger.js';

const config = getConfig();

// Lazy-load OTel to avoid issues if not configured
interface TracerType {
  startActiveSpan: (name: string, fn: (span: SpanType) => Promise<unknown>) => Promise<unknown>;
}

interface SpanType {
  end: () => void;
  recordException: (e: Error) => void;
  setAttribute: (k: string, v: string) => void;
}

let tracer: TracerType | null = null;

async function getTracer() {
  if (tracer) return tracer;

  if (!config.OTEL_EXPORTER_OTLP_ENDPOINT) {
    return null;
  }

  try {
    const { NodeTracerProvider } = await import('@opentelemetry/sdk-trace-node');
    const { OTLPTraceExporter } = await import('@opentelemetry/exporter-trace-otlp-http');
    const { Resource } = await import('@opentelemetry/resources');
    const { SemanticResourceAttributes } = await import('@opentelemetry/semantic-conventions');
    const { registerInstrumentations } = await import('@opentelemetry/instrumentation');
    const { HttpInstrumentation } = await import('@opentelemetry/instrumentation-http');
    const { FastifyInstrumentation } = await import('@opentelemetry/instrumentation-fastify');
    const { GraphQLInstrumentation } = await import('@opentelemetry/instrumentation-graphql');

    const provider = new NodeTracerProvider({
      resource: new Resource({
        [SemanticResourceAttributes.SERVICE_NAME]: config.OTEL_SERVICE_NAME,
      }),
    });

    provider.addSpanProcessor(
      new (await import('@opentelemetry/sdk-trace-base')).BatchSpanProcessor(
        new OTLPTraceExporter({
          url: config.OTEL_EXPORTER_OTLP_ENDPOINT,
        })
      )
    );

    provider.register();

    registerInstrumentations({
      instrumentations: [
        new HttpInstrumentation(),
        new FastifyInstrumentation(),
        new GraphQLInstrumentation(),
      ],
    });

    tracer = provider.getTracer(config.OTEL_SERVICE_NAME);
    logger.info('OpenTelemetry initialized');
  } catch (error) {
    logger.error({ error }, 'Failed to initialize OpenTelemetry');
  }

  return tracer;
}

export function otelPlugin(): ApolloServerPlugin<BaseContext> {
  return {
    async serverWillStart() {
      await getTracer();
    },

    async requestDidStart(requestContext: GraphQLRequestContext<BaseContext>) {
      const activeTracer = await getTracer();
      if (!activeTracer) {
        return {};
      }

      const operationName = requestContext.request.operationName || 'Anonymous';
      const queryHash = hashQuery(requestContext.request.query || '');

      return await activeTracer.startActiveSpan(
        `GraphQL ${operationName}`,
        async (span) => {
          span.setAttribute('graphql.operation.name', operationName);
          span.setAttribute('graphql.operation.type', requestContext.request.operationName ? 'named' : 'anonymous');
          span.setAttribute('graphql.query.hash', queryHash);

          return {
            async didEncounterErrors(errors: readonly Error[]) {
              if (errors.length > 0) {
                span.recordException(errors[0]);
                span.setAttribute('graphql.errors', String(errors.length));
              }
            },
            async willSendResponse() {
              span.end();
            },
          };
        }
      );
    },
  };
}

function hashQuery(query: string): string {
  // Simple hash for query identification (not cryptographic)
  let hash = 0;
  for (let i = 0; i < query.length; i++) {
    const char = query.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash;
  }
  return Math.abs(hash).toString(16);
}