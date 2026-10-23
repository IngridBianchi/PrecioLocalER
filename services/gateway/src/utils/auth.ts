import { getConfig } from './config.js';
import { logger } from './logger.js';
import { createRemoteJWKSet, jwtVerify, type JWTPayload } from 'jose';

const config = getConfig();

export interface AuthUser {
  id: string;
  email: string;
  role?: string;
  [key: string]: unknown;
}

let jwksCache: ReturnType<typeof createRemoteJWKSet> | null = null;

function getJWKS() {
  if (!config.SUPABASE_JWKS_URL) {
    return null;
  }
  if (!jwksCache) {
    jwksCache = createRemoteJWKSet(new URL(config.SUPABASE_JWKS_URL), {
      cacheMaxAge: 600_000, // 10 minutes
      cooldownDuration: 30_000,
      timeoutDuration: 10_000,
    });
  }
  return jwksCache;
}

export async function verifyJWT(token: string): Promise<AuthUser | null> {
  const jwks = getJWKS();

  if (jwks) {
    // Verify with Supabase JWKS (RS256)
    try {
      const { payload } = await jwtVerify(token, jwks, {
        issuer: config.SUPABASE_JWKS_URL?.replace(/\/jwks\.json$/, ''),
        audience: 'authenticated',
      });
      return mapPayloadToUser(payload);
    } catch (error) {
      logger.warn({ error }, 'JWT verification failed with JWKS');
      return null;
    }
  }

  // Fallback: verify with shared secret (HS256) - for development only
  if (config.SUPABASE_JWT_SECRET) {
    try {
      const secret = new TextEncoder().encode(config.SUPABASE_JWT_SECRET);
      const { payload } = await jwtVerify(token, secret, {
        algorithms: ['HS256'],
      });
      return mapPayloadToUser(payload);
    } catch (error) {
      logger.warn({ error }, 'JWT verification failed with secret');
      return null;
    }
  }

  logger.warn('No JWT verification method configured');
  return null;
}

function mapPayloadToUser(payload: JWTPayload): AuthUser {
  return {
    id: payload.sub as string,
    email: payload.email as string,
    role: payload.role as string | undefined,
    ...payload,
  };
}

export function extractBearerToken(authHeader: string | string[] | null | undefined): string | null {
  if (!authHeader) return null;
  const header = Array.isArray(authHeader) ? authHeader[0] : authHeader;
  if (!header) return null;
  const parts = header.split(' ');
  if (parts.length !== 2 || parts[0].toLowerCase() !== 'bearer') {
    return null;
  }
  return parts[1];
}