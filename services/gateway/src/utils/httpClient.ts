import { getConfig } from './config.js';
import { logger } from './logger.js';

const config = getConfig();

export interface ServiceResponse<T> {
  data: T | null;
  error: Error | null;
  status: number;
}

export class ServiceClient {
  private baseUrl: string;
  private serviceName: string;
  private defaultHeaders: Record<string, string>;

  constructor(serviceName: string, baseUrl: string, defaultHeaders: Record<string, string> = {}) {
    this.serviceName = serviceName;
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.defaultHeaders = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      ...defaultHeaders,
    };
  }

  private async request<T>(
    method: string,
    path: string,
    options: {
      body?: unknown;
      headers?: Record<string, string>;
      query?: Record<string, string | number | boolean>;
      signal?: AbortSignal;
    } = {}
  ): Promise<ServiceResponse<T>> {
    const url = new URL(path, this.baseUrl);
    if (options.query) {
      Object.entries(options.query).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          url.searchParams.set(key, String(value));
        }
      });
    }

    const headers = {
      ...this.defaultHeaders,
      ...options.headers,
    };

    const startTime = Date.now();
    const requestLogger = logger.child({
      service: this.serviceName,
      method,
      path,
    });

    try {
      const response = await fetch(url.toString(), {
        method,
        headers,
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: options.signal,
      });

      const duration = Date.now() - startTime;
      let data: T | null = null;

      if (response.status !== 204) {
        const text = await response.text();
        if (text) {
          try {
            data = JSON.parse(text) as T;
          } catch {
            requestLogger.warn({ text }, 'Failed to parse JSON response');
          }
        }
      }

      if (!response.ok) {
        const error = new Error(
          `Service ${this.serviceName} returned ${response.status}: ${response.statusText}`
        ) as Error & { status: number; responseData: unknown };
        error.status = response.status;
        error.responseData = data;
        requestLogger.error({ status: response.status, duration }, 'Service request failed');
        return { data: null, error, status: response.status };
      }

      requestLogger.debug({ duration }, 'Service request succeeded');
      return { data, error: null, status: response.status };
    } catch (error) {
      const duration = Date.now() - startTime;
      const err = error as Error;
      if (err.name === 'AbortError') {
        requestLogger.warn({ duration }, 'Request aborted');
      } else {
        requestLogger.error({ err, duration }, 'Service request error');
      }
      return { data: null, error: err, status: 0 };
    }
  }

  async get<T>(path: string, query?: Record<string, string | number | boolean>, headers?: Record<string, string>) {
    return this.request<T>('GET', path, { query, headers });
  }

  async post<T>(path: string, body?: unknown, headers?: Record<string, string>) {
    return this.request<T>('POST', path, { body, headers });
  }

  async put<T>(path: string, body?: unknown, headers?: Record<string, string>) {
    return this.request<T>('PUT', path, { body, headers });
  }

  async patch<T>(path: string, body?: unknown, headers?: Record<string, string>) {
    return this.request<T>('PATCH', path, { body, headers });
  }

  async delete<T>(path: string, headers?: Record<string, string>) {
    return this.request<T>('DELETE', path, { headers });
  }
}

// Pre-configured clients
export const scraperClient = new ServiceClient('scraper', config.SCRAPER_URL);
export const mlDotnetClient = new ServiceClient('ml-dotnet', config.ML_DOTNET_URL, {
  'X-Internal-Token': config.GATEWAY_INTERNAL_TOKEN || '',
});
export const analyticsJavaClient = new ServiceClient('analytics-java', config.ANALYTICS_JAVA_URL, {
  'X-Internal-Token': config.GATEWAY_INTERNAL_TOKEN || '',
});