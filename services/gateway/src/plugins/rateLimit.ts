import { ApolloServerPlugin, GraphQLRequestContext } from '@apollo/server';
import { BaseContext } from '@apollo/server';
import { getConfig } from '../utils/config.js';
import { logger } from '../utils/logger.js';

const config = getConfig();

// Simple in-memory rate limiter (for production, use Redis)
const rateLimitStore = new Map<string, { count: number; resetAt: number }>();

function parseWindow(window: string): number {
  const match = window.match(/^(\d+)\s*(second|minute|hour)s?$/i);
  if (!match) return 60_000; // default 1 minute
  const value = parseInt(match[1], 10);
  const unit = match[2].toLowerCase();
  switch (unit) {
    case 'second':
      return value * 1000;
    case 'minute':
      return value * 60_000;
    case 'hour':
      return value * 3_600_000;
    default:
      return 60_000;
  }
}

const WINDOW_MS = parseWindow(config.RATE_LIMIT_WINDOW);
const MAX_REQUESTS = config.RATE_LIMIT_MAX;

export function rateLimitPlugin(): ApolloServerPlugin<BaseContext> {
  return {
    async requestDidStart(requestContext: GraphQLRequestContext<BaseContext>) {
      const ip = requestContext.request.http?.headers.get('x-forwarded-for')?.split(',')[0]?.trim()
        || requestContext.request.http?.headers.get('x-real-ip')
        || 'unknown';

      const now = Date.now();
      const key = `ratelimit:${ip}`;
      const record = rateLimitStore.get(key);

      if (!record || now > record.resetAt) {
        rateLimitStore.set(key, { count: 1, resetAt: now + WINDOW_MS });
      } else {
        record.count++;
        if (record.count > MAX_REQUESTS) {
          logger.warn({ ip, count: record.count }, 'Rate limit exceeded');
          throw new Error(`Rate limit exceeded. Max ${MAX_REQUESTS} requests per ${config.RATE_LIMIT_WINDOW}.`);
        }
      }

      return {
        async willSendResponse() {
          const currentRecord = rateLimitStore.get(key);
          if (currentRecord) {
            requestContext.response.http?.headers.set('X-RateLimit-Limit', String(MAX_REQUESTS));
            requestContext.response.http?.headers.set('X-RateLimit-Remaining', String(Math.max(0, MAX_REQUESTS - currentRecord.count)));
            requestContext.response.http?.headers.set('X-RateLimit-Reset', String(Math.ceil(currentRecord.resetAt / 1000)));
          }
        },
      };
    },
  };
}

// Cleanup old entries periodically
setInterval(() => {
  const now = Date.now();
  for (const [key, record] of rateLimitStore.entries()) {
    if (now > record.resetAt) {
      rateLimitStore.delete(key);
    }
  }
}, 60_000);