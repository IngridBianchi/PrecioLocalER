import { EventEmitter } from 'events';
import { Redis as IORedis } from 'ioredis';

const Redis = IORedis;
import { getConfig } from './config.js';
import { logger } from './logger.js';

const config = getConfig();

export interface PubSubIterator<T> {
  next: () => Promise<IteratorResult<T>>;
  return: () => Promise<IteratorResult<T>>;
  throw: (error?: unknown) => Promise<IteratorResult<T>>;
  [Symbol.asyncIterator](): PubSubIterator<T>;
}

class InMemoryPubSub {
  private emitter = new EventEmitter();

  publish(trigger: string, payload: unknown): void {
    this.emitter.emit(trigger, payload);
  }

  asyncIterator<T>(triggers: string[]): PubSubIterator<T> {
    const queue: T[] = [];
    let resolveNext: ((value: void) => void) | null = null;

    const handler = (payload: T) => {
      queue.push(payload);
      if (resolveNext) {
        resolveNext();
        resolveNext = null;
      }
    };

    triggers.forEach((trigger) => this.emitter.on(trigger, handler));

    const iterator: PubSubIterator<T> = {
      next: async () => {
        if (queue.length === 0) {
          await new Promise<void>((resolve) => {
            resolveNext = resolve;
          });
        }
        const value = queue.shift() as T;
        return { value, done: false };
      },
      return: async () => {
        triggers.forEach((trigger) => this.emitter.off(trigger, handler));
        return { value: undefined as unknown as T, done: true };
      },
      throw: async (error) => {
        triggers.forEach((trigger) => this.emitter.off(trigger, handler));
        throw error;
      },
      [Symbol.asyncIterator]() {
        return this;
      },
    };

    return iterator;
  }
}

export const pubsub = new InMemoryPubSub();

// Bridge Redis pub/sub -> in-memory PubSub (optional).
if (config.REDIS_URL) {
  try {
    const subscriber = new Redis(config.REDIS_URL);
    subscriber.on('message', (channel: string, message: string) => {
      try {
        const payload = JSON.parse(message);
        if (channel === 'pl:events:price') {
          pubsub.publish('PRICE_UPDATE', payload);
        } else if (channel === 'pl:events:alert') {
          pubsub.publish('ALERT_TRIGGERED', payload);
        }
      } catch (error) {
        logger.warn({ channel, error }, 'Failed to parse Redis pub/sub message');
      }
    });
    subscriber.subscribe('pl:events:price', 'pl:events:alert', (err) => {
      if (err) {
        logger.error({ err }, 'Failed to subscribe to Redis channels');
      } else {
        logger.info('Gateway subscribed to Redis price/alert channels');
      }
    });
  } catch (error) {
    logger.error({ error }, 'Failed to initialize Redis subscriber');
  }
}
