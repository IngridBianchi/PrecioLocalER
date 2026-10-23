import DataLoader from 'dataloader';
import { scraperClient, mlDotnetClient, analyticsJavaClient } from '../utils/httpClient.js';
import { logger } from '../utils/logger.js';

// ============================================================
// Types
// ============================================================

export interface Product {
  id: string;
  name: string;
  normalizedName: string;
  brand?: { id: string; name: string; slug: string } | string;
  category?: { id: string; name: string; slug: string } | string;
  sizeQuantity: number;
  sizeUnit: string;
  ean?: string;
  prices?: PricePoint[];
}

export interface PricePoint {
  id: string;
  productId: string;
  chain: string;
  chainId: number;
  skuId?: number;
  storeId?: number;
  price: string;
  promoType?: string;
  capturedAt: string;
  scrapedAt: string;
  hash: string;
}

export interface Chain {
  id: string;
  name: string;
  slug: string;
  domain?: string;
  base_url?: string;
  region: string;
  active: boolean;
  scrape_schedule?: string;
}

export interface AlertEvent {
  id: string;
  alert_id: string;
  price_point_id: string;
  price: string;
  triggered_at: string;
}

export interface ForecastPoint {
  date: string;
  predicted_price: number;
  conf_low: number;
  conf_high: number;
}

export interface ForecastResponse {
  product_id: string;
  chain: string;
  requested_model: string;
  model: string;
  degraded_reason?: string;
  n_points: number;
  mape?: number;
  rmse?: number;
  mae?: number;
  points: ForecastPoint[];
}

export interface AnomaliesResponse {
  product_id: string;
  chain: string;
  n_points: number;
  n_anomalies: number;
  detector: string;
  anomalies: Array<{
    id: string;
    product_id: string;
    chain: string;
    price_point_id: string;
    price: string;
    expected_price: string;
    score: number;
    detected_at: string;
  }>;
}

// ============================================================
// DataLoader Factory Functions
// ============================================================

/**
 * Transforms scraper product data (snake_case) to GraphQL format (camelCase)
 */
function transformProduct(data: any): Product | null {
  if (!data) return null;
  return {
    id: data.id,
    name: data.name,
    normalizedName: data.normalized_name ?? data.normalizedName,
    brand: data.brand,
    category: data.category,
    sizeQuantity: data.size_quantity ?? data.sizeQuantity,
    sizeUnit: data.size_unit ?? data.sizeUnit,
    ean: data.ean,
    prices: data.prices?.map(transformPricePoint) || [],
  };
}

/**
 * Transforms scraper price point data (snake_case) to GraphQL format (camelCase)
 */
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

/**
 * Creates a DataLoader for fetching products by IDs
 * Batches requests to scraper service
 */
export function createProductLoader() {
  return new DataLoader<string, Product | null>(async (ids) => {
    const uniqueIds = [...new Set(ids)];
    logger.debug({ count: uniqueIds.length }, 'Batch loading products');

    // For now, fetch individually (scraper doesn't have batch endpoint)
    // In production, you'd want a batch endpoint on the scraper
    const results = await Promise.all(
      uniqueIds.map(async (id) => {
        const { data, error } = await scraperClient.get<any>(`/products/${id}`);
        if (error) {
          logger.warn({ id, error: error.message }, 'Failed to load product');
          return null;
        }
        return transformProduct(data);
      })
    );

    // Map back to original order
    const productMap = new Map(uniqueIds.map((id, i) => [id, results[i]]));
    return ids.map((id) => productMap.get(id) ?? null);
  });
}

/**
 * Creates a DataLoader for fetching price points by product IDs
 * Returns latest price point per chain for each product
 */
export function createPricePointLoader() {
  return new DataLoader<string, PricePoint[]>(async (productIds) => {
    const uniqueIds = [...new Set(productIds)];
    logger.debug({ count: uniqueIds.length }, 'Batch loading price points');

    const results = await Promise.all(
      uniqueIds.map(async (id) => {
        const { data, error } = await scraperClient.get<any[]>(`/products/${id}/history`, {
          span: '90d',
        });
        if (error) {
          logger.warn({ productId: id, error: error.message }, 'Failed to load price history');
          return [];
        }
        // Get latest price point per chain and transform to GraphQL format
        const latestPerChain = new Map<string, PricePoint>();
        for (const pp of data || []) {
          const existing = latestPerChain.get(pp.chain);
          const capturedAt = pp.captured_at ?? pp.capturedAt;
          if (!existing || new Date(capturedAt) > new Date(existing.capturedAt)) {
            latestPerChain.set(pp.chain, transformPricePoint(pp));
          }
        }
        return Array.from(latestPerChain.values());
      })
    );

    const priceMap = new Map(uniqueIds.map((id, i) => [id, results[i]]));
    return productIds.map((id) => priceMap.get(id) ?? []);
  });
}

/**
 * Creates a DataLoader for fetching chains
 */
export function createChainLoader() {
  return new DataLoader<string, Chain | null>(async (ids) => {
    const uniqueIds = [...new Set(ids)];
    logger.debug({ count: uniqueIds.length }, 'Batch loading chains');

    // Chains are static-ish, fetch all once and filter
    const { data, error } = await scraperClient.get<Chain[]>('/chains');
    if (error) {
      logger.warn({ error: error.message }, 'Failed to load chains');
      return uniqueIds.map(() => null);
    }

    const chainMap = new Map((data || []).map((c) => [c.slug, c]));
    return uniqueIds.map((id) => chainMap.get(id) ?? null);
  });
}

/**
 * Creates a DataLoader for fetching forecast by product ID
 */
export function createForecastLoader() {
  return new DataLoader<string, ForecastResponse | null>(async (ids) => {
    const uniqueIds = [...new Set(ids)];
    logger.debug({ count: uniqueIds.length }, 'Batch loading forecasts');

    const results = await Promise.all(
      uniqueIds.map(async (id) => {
        const { data, error } = await scraperClient.get<ForecastResponse>(`/ml/forecast/${id}`);
        if (error) {
          logger.warn({ productId: id, error: error.message }, 'Failed to load forecast');
          return null;
        }
        return data;
      })
    );

    const forecastMap = new Map(uniqueIds.map((id, i) => [id, results[i]]));
    return ids.map((id) => forecastMap.get(id) ?? null);
  });
}

/**
 * Creates a DataLoader for fetching ML.NET forecast
 */
export function createMlNetForecastLoader() {
  return new DataLoader<string, ForecastResponse | null>(async (ids) => {
    const uniqueIds = [...new Set(ids)];
    logger.debug({ count: uniqueIds.length }, 'Batch loading ML.NET forecasts');

    const results = await Promise.all(
      uniqueIds.map(async (id) => {
        const { data, error } = await mlDotnetClient.get<ForecastResponse>(`/api/forecast/${id}`);
        if (error) {
          logger.warn({ productId: id, error: error.message }, 'Failed to load ML.NET forecast');
          return null;
        }
        return data;
      })
    );

    const forecastMap = new Map(uniqueIds.map((id, i) => [id, results[i]]));
    return ids.map((id) => forecastMap.get(id) ?? null);
  });
}

/**
 * Creates a DataLoader for fetching alert events by user ID
 */
export function createAlertEventLoader() {
  return new DataLoader<string, AlertEvent[]>(async (userIds) => {
    const uniqueIds = [...new Set(userIds)];
    logger.debug({ count: uniqueIds.length }, 'Batch loading alert events');

    const results = await Promise.all(
      uniqueIds.map(async (id) => {
        const { data, error } = await analyticsJavaClient.get<AlertEvent[]>('/api/alert-events', {
          userId: id,
        });
        if (error) {
          logger.warn({ userId: id, error: error.message }, 'Failed to load alert events');
          return [];
        }
        return data || [];
      })
    );

    const eventsMap = new Map(uniqueIds.map((id, i) => [id, results[i]]));
    return userIds.map((id) => eventsMap.get(id) ?? []);
  });
}

// ============================================================
// Context DataLoaders
// ============================================================

export interface DataLoaders {
  products: ReturnType<typeof createProductLoader>;
  pricePoints: ReturnType<typeof createPricePointLoader>;
  chains: ReturnType<typeof createChainLoader>;
  forecasts: ReturnType<typeof createForecastLoader>;
  mlNetForecasts: ReturnType<typeof createMlNetForecastLoader>;
  alertEvents: ReturnType<typeof createAlertEventLoader>;
}

export function createDataLoaders(): DataLoaders {
  return {
    products: createProductLoader(),
    pricePoints: createPricePointLoader(),
    chains: createChainLoader(),
    forecasts: createForecastLoader(),
    mlNetForecasts: createMlNetForecastLoader(),
    alertEvents: createAlertEventLoader(),
  };
}