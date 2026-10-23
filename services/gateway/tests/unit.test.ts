import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createDataLoaders } from '../src/datasources/loaders.js';
import { verifyJWT, extractBearerToken } from '../src/utils/auth.js';

// Mock fetch globally
const mockFetch = vi.fn();
global.fetch = mockFetch;

describe('DataLoaders', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('should create all data loaders', () => {
    const loaders = createDataLoaders();
    expect(loaders.products).toBeDefined();
    expect(loaders.pricePoints).toBeDefined();
    expect(loaders.chains).toBeDefined();
    expect(loaders.forecasts).toBeDefined();
    expect(loaders.mlNetForecasts).toBeDefined();
    expect(loaders.alertEvents).toBeDefined();
  });
});

describe('Auth Utilities', () => {
  it('should extract bearer token', () => {
    expect(extractBearerToken('Bearer abc123')).toBe('abc123');
    expect(extractBearerToken('bearer abc123')).toBe('abc123');
    // RFC 6750: el credential no puede contener espacios; un header partido
    // en más de dos segmentos es inválido y no debe tratarse como token.
    expect(extractBearerToken('Bearer abc 123')).toBeNull();
    expect(extractBearerToken('Basic abc123')).toBeNull();
    expect(extractBearerToken('abc123')).toBeNull();
    expect(extractBearerToken(undefined)).toBeNull();
  });

  it('should handle missing JWT config gracefully', async () => {
    // Without SUPABASE_JWT_SECRET or SUPABASE_JWKS_URL, verifyJWT returns null
    vi.stubEnv('SUPABASE_JWT_SECRET', '');
    vi.stubEnv('SUPABASE_JWKS_URL', '');

    const { verifyJWT } = await import('../src/utils/auth.js');
    const result = await verifyJWT('any-token');
    expect(result).toBeNull();
  });
});

describe('Schema Types', () => {
  it('should have expected types', () => {
    // This is a placeholder to ensure test file runs
    expect(true).toBe(true);
  });
});