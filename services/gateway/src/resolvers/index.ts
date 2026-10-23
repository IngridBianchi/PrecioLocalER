import { GraphQLError } from 'graphql';
import {
  ScraperDataSource,
  MlDotnetDataSource,
  AnalyticsJavaDataSource,
  DataLoaders,
  createDataLoaders,
  PricePoint,
} from '../datasources/index.js';
import { verifyJWT, extractBearerToken, AuthUser } from '../utils/auth.js';
import { logger } from '../utils/logger.js';
import { pubsub } from '../utils/pubsub.js';

function transformPricePoint(pp: any): PricePoint {
  return {
    id: pp.id,
    productId: pp.product_id ?? pp.productId,
    chain: pp.chain,
    chainId: pp.chain_id ?? pp.chainId,
    skuId: pp.sku_id ?? pp.skuId,
    storeId: pp.store_id ?? pp.storeId,
    price: pp.price,
    promoType: pp.promo_type ?? pp.promoType,
    capturedAt: pp.captured_at ?? pp.capturedAt,
    scrapedAt: pp.scraped_at ?? pp.scrapedAt,
    hash: pp.hash,
  };
}

// ============================================================
// Context Type
// ============================================================

export interface Context {
  user: AuthUser | null;
  dataSources: {
    scraper: ScraperDataSource;
    mlDotnet: MlDotnetDataSource;
    analyticsJava: AnalyticsJavaDataSource;
  };
  loaders: DataLoaders;
  req: RequestLike;
}

// ============================================================
// Helper Functions
// ============================================================

function requireAuth(context: Context): AuthUser {
  if (!context.user) {
    throw new GraphQLError('Authentication required', {
      extensions: { code: 'UNAUTHENTICATED' },
    });
  }
  return context.user;
}

function requireAdmin(context: Context): AuthUser {
  const user = requireAuth(context);
  if (user.role !== 'admin' && user.role !== 'service') {
    throw new GraphQLError('Admin access required', {
      extensions: { code: 'FORBIDDEN' },
    });
  }
  return user;
}

// ============================================================
// Resolvers
// ============================================================

export const resolvers = {
  Query: {
    health: async (_: unknown, __: unknown, context: Context) => {
      const [scraper, mlDotnet, analyticsJava] = await Promise.all([
        context.dataSources.scraper.health(),
        context.dataSources.mlDotnet.health(),
        context.dataSources.analyticsJava.health(),
      ]);

      return {
        status: 'ok',
        service: 'gateway',
        timestamp: new Date().toISOString(),
        checks: {
          scraper: scraper.status === 'ok' || scraper.status === 'healthy',
          mlDotnet: mlDotnet.status === 'ok' || mlDotnet.status === 'healthy',
          analyticsJava: analyticsJava.status === 'ok' || analyticsJava.status === 'healthy',
          postgres: true, // checked via individual services
          redis: true,
          mongo: true,
        },
      };
    },

    searchProducts: async (
      _: unknown,
      { q, chain, limit = 50, offset = 0 }: { q: string; chain?: string; limit?: number; offset?: number },
      context: Context
    ) => {
      const result = await context.dataSources.scraper.searchProducts(q, chain, limit, offset);
      return {
        products: result.products,
        total: result.total,
        page: Math.floor(offset / limit) + 1,
        pageSize: limit,
      };
    },

    product: async (_: unknown, { id }: { id: string }, context: Context) => {
      return context.loaders.products.load(id);
    },

    compare: async (_: unknown, { productIds }: { productIds: string[] }, context: Context) => {
      const products = await Promise.all(productIds.map((id) => context.loaders.products.load(id)));

      return products.map((product) => {
        if (!product) return null;
        return {
          product,
          prices: [], // Will be resolved by PriceComparison resolver
        };
      }).filter(Boolean);
    },

    priceHistory: async (
      _: unknown,
      { productId, span = '30d', chain }: { productId: string; span?: string; chain?: string },
      context: Context
    ) => {
      const pricePoints = await context.dataSources.scraper.getProductHistory(productId, span, chain);
      return pricePoints.map(transformPricePoint);
    },

    forecast: async (
      _: unknown,
      { productId, chain, horizon = 7 }: { productId: string; chain?: string; horizon?: number },
      context: Context
    ) => {
      return context.loaders.forecasts.load(productId);
    },

    forecastComparison: async (
      _: unknown,
      { productId, chain }: { productId: string; chain: string },
      context: Context
    ) => {
      return context.dataSources.mlDotnet.getComparison(productId, chain);
    },

    anomalies: async (
      _: unknown,
      { productId, chain, days = 30 }: { productId: string; chain?: string; days?: number },
      context: Context
    ) => {
      return context.dataSources.scraper.getAnomalies(productId, chain, days);
    },

    inflationReport: async (
      _: unknown,
      { span = '30d' }: { span?: string },
      context: Context
    ) => {
      const report = await context.dataSources.mlDotnet.getInflationReport(span);
      if (!report) {
        return {
          period: 'MONTH',
          span,
          categories: [],
          overallInflation: 0,
          generatedAt: new Date().toISOString(),
        };
      }
      return {
        period: report.period || 'MONTH',
        span: report.span || span,
        categories: (report.categories || []).map((category) => ({
          category: {
            id: category.categorySlug,
            name: category.categoryName,
            slug: category.categorySlug,
          },
          inflation: category.inflation,
          productCount: category.productCount,
          avgPriceChange: category.avgPriceChange,
        })),
        overallInflation: report.overallInflation,
        generatedAt: report.generatedAt,
      };
    },

    topMovers: async (
      _: unknown,
      { span = '30d', top = 20 }: { span?: string; top?: number },
      context: Context
    ) => {
      const report = await context.dataSources.mlDotnet.getTopMovers(span, top);
      if (!report) {
        return {
          period: 'MONTH',
          span,
          gainers: [],
          losers: [],
          generatedAt: new Date().toISOString(),
        };
      }
      const mapMover = (mover: any) => ({
        product: { id: String(mover.productId), name: mover.productName },
        chain: { name: mover.chainName, slug: mover.chainName.toLowerCase().replace(/\\s+/g, '-'), active: true },
        priceChange: mover.priceChange,
        priceChangePct: mover.priceChangePct,
        currentPrice: mover.currentPrice,
        previousPrice: mover.previousPrice,
      });
      return {
        period: report.period || 'MONTH',
        span: report.span || span,
        gainers: (report.gainers || []).map(mapMover),
        losers: (report.losers || []).map(mapMover),
        generatedAt: report.generatedAt,
      };
    },

    categoryReport: async (
      _: unknown,
      { categorySlug, chain, limit = 50 }: { categorySlug: string; chain?: string; limit?: number },
      context: Context
    ) => {
      const rows = await context.dataSources.scraper.getCategoryPrices(categorySlug, chain, limit);
      return rows.map((row: any) => ({
        id: String(row.id),
        productId: String(row.product_id ?? row.productId),
        productName: row.product_name ?? row.productName,
        chain: row.chain,
        chainId: row.chain_id ?? row.chainId,
        price: row.price,
        promoType: row.promo_type ?? row.promoType,
        capturedAt: row.captured_at ?? row.capturedAt,
      }));
    },

    chains: async (_: unknown, __: unknown, context: Context) => {
      return context.dataSources.scraper.getChains();
    },

    myAlerts: async (_: unknown, __: unknown, context: Context) => {
      const user = requireAuth(context);
      const alerts = await context.dataSources.analyticsJava.getAlerts(user.id);
      return alerts.map((alert) => ({
        id: alert.id,
        userId: alert.userId,
        productId: alert.productId,
        direction: alert.direction,
        targetPrice: alert.targetPrice,
        active: alert.active,
        lastTriggeredAt: alert.lastTriggeredAt,
        createdAt: alert.createdAt,
      }));
    },

    alertEvents: async (
      _: unknown,
      { limit = 50 }: { limit?: number },
      context: Context
    ) => {
      const user = requireAuth(context);
      const events = await context.loaders.alertEvents.load(user.id);
      return events.slice(0, limit);
    },
  },

  Mutation: {
    createAlert: async (
      _: unknown,
      { input }: { input: { productId: string; direction: 'BELOW' | 'ABOVE'; targetPrice: string } },
      context: Context
    ) => {
      const user = requireAuth(context);
      const alert = await context.dataSources.analyticsJava.createAlert(
        user.id,
        input.productId,
        input.direction,
        input.targetPrice
      );
      if (!alert) {
        throw new GraphQLError('Failed to create alert', {
          extensions: { code: 'INTERNAL_SERVER_ERROR' },
        });
      }
      return {
        id: alert.id,
        userId: alert.userId,
        productId: alert.productId,
        direction: alert.direction,
        targetPrice: alert.targetPrice,
        active: alert.active,
        lastTriggeredAt: alert.lastTriggeredAt,
        createdAt: alert.createdAt,
      };
    },

    toggleAlert: async (
      _: unknown,
      { id, active }: { id: string; active: boolean },
      context: Context
    ) => {
      requireAuth(context);
      const alert = await context.dataSources.analyticsJava.toggleAlert(id, active);
      if (!alert) {
        throw new GraphQLError('Failed to toggle alert', {
          extensions: { code: 'INTERNAL_SERVER_ERROR' },
        });
      }
      return {
        id: alert.id,
        userId: alert.userId,
        productId: alert.productId,
        direction: alert.direction,
        targetPrice: alert.targetPrice,
        active: alert.active,
        lastTriggeredAt: alert.lastTriggeredAt,
        createdAt: alert.createdAt,
      };
    },

    deleteAlert: async (_: unknown, { id }: { id: string }, context: Context) => {
      requireAuth(context);
      const success = await context.dataSources.analyticsJava.deleteAlert(id);
      return { success, message: success ? 'Deleted' : 'Failed to delete' };
    },

    runScrape: async (
      _: unknown,
      { chain }: { chain: string },
      context: Context
    ) => {
      requireAdmin(context);
      const adminToken = process.env.ADMIN_TOKEN || '';
      const job = await context.dataSources.scraper.runScrape(chain, adminToken);
      return job;
    },

    retrainModels: async (
      _: unknown,
      { chain }: { chain?: string },
      context: Context
    ) => {
      requireAdmin(context);
      const adminToken = process.env.ADMIN_TOKEN || '';
      try {
        const response = await fetch(`${process.env.SCRAPER_URL || 'http://scraper:8000'}/admin/retrain`, {
          method: 'POST',
          headers: { 'X-Admin-Token': adminToken },
        });
        if (!response.ok) {
          return { success: false, message: `Retrain failed: ${response.status}` };
        }
        return { success: true, message: 'Retraining triggered' };
      } catch (error) {
        return { success: false, message: `Retrain error: ${(error as Error).message}` };
      }
    },
  },

  Subscription: {
    onPriceUpdate: {
      subscribe: async (_: unknown, { productId }: { productId: string }, _context: Context) => {
        const iterator = pubsub.asyncIterator<any>(['PRICE_UPDATE']);
        const filtered: AsyncIterable<any> = {
          [Symbol.asyncIterator]() {
            const inner = iterator[Symbol.asyncIterator]();
            return {
              async next() {
                while (true) {
                  const result = await inner.next();
                  if (result.done) return result;
                  const payload = result.value;
                  if (String(payload?.productId ?? payload?.product_id) === String(productId)) {
                    return { value: transformPricePoint(payload), done: false };
                  }
                }
              },
              async return() {
                return inner.return ? inner.return() : { value: undefined, done: true };
              },
              async throw(error) {
                return inner.throw ? inner.throw(error) : { value: undefined, done: true };
              },
            };
          },
        };
        return filtered;
      },
    },

    onAlertTriggered: {
      subscribe: async (_: unknown, __: unknown, context: Context) => {
        const user = requireAuth(context);
        const iterator = pubsub.asyncIterator<any>(['ALERT_TRIGGERED']);
        const filtered: AsyncIterable<any> = {
          [Symbol.asyncIterator]() {
            const inner = iterator[Symbol.asyncIterator]();
            return {
              async next() {
                while (true) {
                  const result = await inner.next();
                  if (result.done) return result;
                  const payload = result.value;
                  const alertUserId = payload?.alert?.userId ?? payload?.alert?.user_id ?? payload?.userId;
                  if (String(alertUserId) === String(user.id)) {
                    return { value: payload, done: false };
                  }
                }
              },
              async return() {
                return inner.return ? inner.return() : { value: undefined, done: true };
              },
              async throw(error) {
                return inner.throw ? inner.throw(error) : { value: undefined, done: true };
              },
            };
          },
        };
        return filtered;
      },
    },
  },

  // ============================================================
  // Field Resolvers
  // ============================================================

  Product: {
    skus: async (product: { id: string }, { chain }: { chain?: string }, context: Context) => {
      // TODO: Implement when scraper adds SKU endpoint
      return [];
    },
    prices: async (product: { id: string; prices?: any[] }, _: unknown, context: Context) => {
      // Return prices from DataLoader if available (scraper returns them in product detail)
      if (product.prices && product.prices.length > 0) {
        return product.prices;
      }
      // Fallback: fetch from history if not in product detail
      const pricePoints = await context.loaders.pricePoints.load(product.id);
      return pricePoints;
    },
  },

  PriceComparison: {
    prices: async (comparison: { product: { id: string } }, _: unknown, context: Context) => {
      const pricePoints = await context.loaders.pricePoints.load(comparison.product.id);
      return pricePoints.map((pp) => ({
        chain: { slug: pp.chain, name: pp.chain, active: true },
        price: pp.price,
        promoType: pp.promoType,
        capturedAt: pp.capturedAt,
      }));
    },
  },

  Alert: {
    product: async (alert: { productId: string }, _: unknown, context: Context) => {
      return context.loaders.products.load(alert.productId);
    },
  },

  AlertEvent: {
    alert: async (event: { alert_id: string }, _: unknown, context: Context) => {
      // TODO: Load alert by ID
      return null;
    },
    pricePoint: async (event: { price_point_id: string }, _: unknown, context: Context) => {
      // TODO: Load price point by ID
      return null;
    },
  },
};

// ============================================================
// Context Factory
// ============================================================

// Minimal interface for request-like objects (Fetch Request or Fastify IncomingMessage)
interface RequestLike {
  headers: {
    get(name: string): string | string[] | null | undefined;
  };
  body?: unknown;
}

export async function createContext(req: RequestLike): Promise<Context> {
  // Extract and verify JWT
  const authHeader = req.headers.get('authorization');
  const token = extractBearerToken(authHeader);
  let user: AuthUser | null = null;

  if (token) {
    user = await verifyJWT(token);
    if (!user) {
      logger.warn({ token: token.substring(0, 10) + '...' }, 'Invalid JWT token');
    }
  }

  // Create data sources
  const dataSources = {
    scraper: new ScraperDataSource(),
    mlDotnet: new MlDotnetDataSource(),
    analyticsJava: new AnalyticsJavaDataSource(),
  };

  // Create DataLoaders
  const loaders = createDataLoaders();

  return {
    user,
    dataSources,
    loaders,
    req,
  };
}