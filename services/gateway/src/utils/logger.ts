import pino from 'pino';
import { getConfig } from './config.js';

const config = getConfig();

export const logger = pino({
  level: config.LOG_LEVEL,
  transport: config.NODE_ENV === 'development'
    ? {
        target: 'pino-pretty',
        options: {
          colorize: true,
          translateTime: 'SYS:standard',
          ignore: 'pid,hostname',
        },
      }
    : undefined,
  formatters: {
    level: (label) => {
      return { level: label };
    },
  },
  timestamp: pino.stdTimeFunctions.isoTime,
  base: {
    service: config.OTEL_SERVICE_NAME,
  },
});

export function createChildLogger(bindings: Record<string, unknown>) {
  return logger.child(bindings);
}