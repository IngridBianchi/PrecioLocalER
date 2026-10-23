import { describe, it, expect, vi, beforeAll, beforeEach, afterAll } from 'vitest';
import { createContext } from '../src/resolvers/index.js';
import { resolvers } from '../src/resolvers/index.js';

// Mock fetch for integration tests
const mockFetch = vi.fn();
global.fetch = mockFetch;

describe('GraphQL Resolvers Integration', () => {
  beforeAll(() => {
    vi.stubEnv('NODE_ENV', 'test');
    vi.stubEnv('SCRAPER_URL', 'http://localhost:8000');
    vi.stubEnv('ML_DOTNET_URL', 'http://localhost:8001');
    vi.stubEnv('ANALYTICS_JAVA_URL', 'http://localhost:8080');
  });

  afterAll(() => {
    vi.unstubAllEnvs();
  });

  beforeEach(() => {
    vi.resetAllMocks();
    // Default mock responses
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
      text: async () => '{}',
    });
  });

  it('should create context with user from valid JWT', async () => {
    // Mock a valid JWT response
    const mockRequest = new Request('http://localhost/graphql', {
      method: 'POST',
      headers: {
        'authorization': 'Bearer valid-token',
        'content-type': 'application/json',
      },
    });

    const context = await createContext(mockRequest);
    expect(context).toBeDefined();
    expect(context.dataSources).toBeDefined();
    expect(context.loaders).toBeDefined();
    expect(context.req).toBe(mockRequest);
  });

  it('should create context without user when no auth header', async () => {
    const mockRequest = new Request('http://localhost/graphql', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
      },
    });

    const context = await createContext(mockRequest);
    expect(context.user).toBeNull();
  });

  it('should have health resolver', async () => {
    const mockRequest = new Request('http://localhost/graphql', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
    });
    const context = await createContext(mockRequest);

    // Mock health responses (httpClient lee `response.text()`)
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ status: 'healthy' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ status: 'healthy', mklAvailable: true }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify({ status: 'healthy', checks: { postgres: true, mongodb: true } }),
      });

    const result = await resolvers.Query.health(null, {}, context);
    expect(result).toBeDefined();
    expect(result.status).toBe('ok');
    expect(result.checks.scraper).toBe(true);
    expect(result.checks.mlDotnet).toBe(true);
    expect(result.checks.analyticsJava).toBe(true);
  });

  it('should require auth for myAlerts', async () => {
    const mockRequest = new Request('http://localhost/graphql', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
    });
    const context = await createContext(mockRequest);

    await expect(
      resolvers.Query.myAlerts(null, {}, context)
    ).rejects.toThrow('Authentication required');
  });

  it('should require admin for runScrape', async () => {
    const mockRequest = new Request('http://localhost/graphql', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
    });
    const context = await createContext(mockRequest);

    await expect(
      resolvers.Mutation.runScrape(null, { chain: 'la_anonima' }, context)
    ).rejects.toThrow('Authentication required');
  });
});