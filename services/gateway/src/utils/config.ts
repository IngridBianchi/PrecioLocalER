import { z } from 'zod';

export const configSchema = z.object({
  // Server
  PORT: z.coerce.number().default(4000),
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
  LOG_LEVEL: z.enum(['fatal', 'error', 'warn', 'info', 'debug', 'trace']).default('info'),

  // Downstream services
  SCRAPER_URL: z.string().url().default('http://scraper:8000'),
  ML_DOTNET_URL: z.string().url().default('http://ml-dotnet:8001'),
  ANALYTICS_JAVA_URL: z.string().url().default('http://analytics-java:8080'),

  // Auth - Supabase JWT
  SUPABASE_JWT_SECRET: z.string().optional(),
  SUPABASE_JWKS_URL: z.string().url().optional().or(z.literal('')),
  GATEWAY_INTERNAL_TOKEN: z.string().optional(),

  // Rate limiting
  RATE_LIMIT_MAX: z.coerce.number().default(100),
  RATE_LIMIT_WINDOW: z.string().default('1 minute'),

  // Redis (for rate limit / cache if needed)
  REDIS_URL: z.string().optional(),

  // Observability
  OTEL_EXPORTER_OTLP_ENDPOINT: z.string().url().optional().or(z.literal('')),
  OTEL_SERVICE_NAME: z.string().default('gateway'),
});

export type Config = z.infer<typeof configSchema>;

let cachedConfig: Config | null = null;

export function getConfig(): Config {
  if (cachedConfig) return cachedConfig;

  const result = configSchema.safeParse(process.env);
  if (!result.success) {
    console.error('❌ Invalid configuration:', result.error.flatten().fieldErrors);
    process.exit(1);
  }

  cachedConfig = result.data;
  return cachedConfig;
}

export function resetConfig() {
  cachedConfig = null;
}