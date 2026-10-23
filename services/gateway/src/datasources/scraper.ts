import { scraperClient } from '../utils/httpClient.js';
import { logger } from '../utils/logger.js';
import { z } from 'zod';

// ============================================================
// Types
// ============================================================

// Zod schema for chain validation (matches actual scraper response)
const ChainValidationSchema = z.object({
  id: z.union([z.string(), z.number()]).transform(String),
  name: z.string(),
  slug: z.string(),
  domain: z.string().nullable().optional(),
  base_url: z.string().nullable().optional(),
  region: z.string().nullable().optional(),
  active: z.boolean(),
  scrape_schedule: z.string().nullable().optional(),
});

const ChainsValidationSchema = z.array(ChainValidationSchema);

export interface Product {
  id: string;
  name: string;
  normalized_name: string;
  brand?: { id: string; name: string; slug: string };
  category?: { id: string; name: string; slug: string };
  size_quantity: number;
  size_unit: string;
  ean?: string;
}

export interface PricePoint {
  id: string;
  product_id: string;
  chain: string;
  chain_id: number;
  sku_id?: number;
  store_id?: number;
  price: string;
  promo_type?: string;
  captured_at: string;
  scraped_at: string;
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

export interface ProductSearchHit {
  id: string;
  name: string;
  normalizedName: string;
  brand: string | null;
  category: string | null;
  categorySlug: string | null;
  sizeQuantity: number | null;
  sizeUnit: string;
  ean: string | null;
  chain: string;
  chainName: string;
  price: string;
  promoType: string | null;
  capturedAt: string;
}

export interface SearchResult {
  products: ProductSearchHit[];
  total: number;
  page: number;
  page_size: number;
}

export interface ScrapeJobResponse {
  chain: string;
  status: string;
  products_found: number;
  price_points_inserted: number;
  errors: number;
  started_at: string;
  finished_at?: string;
}

export interface HealthResponse {
  status: string;
  checks?: Record<string, boolean>;
}

// ============================================================
// Scraper DataSource
// ============================================================

export class ScraperDataSource {
  async health(): Promise<HealthResponse> {
    const { data, error } = await scraperClient.get<HealthResponse>('/health');
    if (error) {
      logger.error({ error: error.message }, 'Scraper health check failed');
      return { status: 'unhealthy', checks: { database: false } };
    }
    return data || { status: 'unknown' };
  }

  async ready(): Promise<{ status: string; checks: { database: boolean } }> {
    const { data, error } = await scraperClient.get<{ status: string; checks: { database: boolean } }>('/ready');
    if (error) {
      return { status: 'not_ready', checks: { database: false } };
    }
    return data || { status: 'not_ready', checks: { database: false } };
  }

  async searchProducts(
    query: string,
    chain?: string,
    limit = 20,
    offset = 0
  ): Promise<SearchResult> {
    // Scraper returns paginated response with products and total
    const { data, error } = await scraperClient.get<{ products: any[]; total: number }>('/products', {
      q: query,
      chain,
      limit,
      offset,
    });

    if (error) {
      logger.error({ query, chain, error: error.message }, 'Product search failed');
      return { products: [], total: 0, page: 1, page_size: limit };
    }

    const products = data?.products || [];
    const total = data?.total || 0;

    // Transform snake_case scraper fields to camelCase GraphQL fields
    const transformed = products.map((p: any) => ({
      id: p.id,
      name: p.name,
      normalizedName: p.normalized_name,
      brand: p.brand,
      category: p.category,
      categorySlug: p.category_slug,
      sizeQuantity: p.size_quantity ? parseFloat(p.size_quantity) : null,
      sizeUnit: p.size_unit,
      ean: p.ean,
      chain: p.chain,
      chainName: p.chain_name,
      price: p.price,
      promoType: p.promo_type,
      capturedAt: p.captured_at,
    }));

    return {
      products: transformed,
      total,
      page: Math.floor(offset / limit) + 1,
      page_size: limit,
    };
  }

  async getProduct(id: string): Promise<Product | null> {
    const { data, error } = await scraperClient.get<Product>(`/products/${id}`);
    if (error) {
      logger.warn({ id, error: error.message }, 'Product not found');
      return null;
    }
    return data;
  }

  async getProductHistory(
    productId: string,
    span = '30d',
    chain?: string
  ): Promise<PricePoint[]> {
    const { data, error } = await scraperClient.get<PricePoint[]>(`/products/${productId}/history`, {
      span,
      chain,
    });
    if (error) {
      logger.warn({ productId, error: error.message }, 'Failed to get price history');
      return [];
    }
    return data || [];
  }

  async getCategoryPrices(categorySlug: string, chain?: string, limit = 50): Promise<any[]> {
    const { data, error } = await scraperClient.get<any[]>(`/categories/${categorySlug}/prices`, {
      chain,
      limit,
    });
    if (error) {
      logger.warn({ categorySlug, error: error.message }, 'Failed to get category prices');
      return [];
    }
    return data || [];
  }

async getChains(): Promise<Chain[]> {
    const { data, error } = await scraperClient.get<Chain[]>('/chains');
    if (error) {
      logger.error({ error: error.message }, 'Failed to get chains');
      return [];
    }
    if (!data) {
      logger.warn('Chains response is null/undefined');
      return [];
    }

    // Validate response with Zod schema
    const validationResult = ChainsValidationSchema.safeParse(data);
    if (!validationResult.success) {
      logger.error({
        error: validationResult.error.message,
        received: JSON.stringify(data).slice(0, 500)
      }, 'Chains response validation failed');
      // Return empty array on validation failure rather than throwing
      // This prevents the gateway from crashing if the scraper returns malformed data
      return [];
    }

    // Cast validated data to Chain[] (validation ensures structure matches)
    return validationResult.data as Chain[];
  }

  async getForecast(
    productId: string,
    chain?: string,
    horizon = 7
  ): Promise<Record<string, unknown> | null> {
    const { data, error } = await scraperClient.get<Record<string, unknown>>(`/ml/forecast/${productId}`, {
      chain,
      horizon,
    });
    if (error) {
      logger.warn({ productId, error: error.message }, 'Failed to get forecast');
      return null;
    }
    return data;
  }

  async getAnomalies(
    productId: string,
    chain?: string,
    days = 30
  ): Promise<Record<string, unknown> | null> {
    const { data, error } = await scraperClient.get<Record<string, unknown>>(`/ml/anomalies/${productId}`, {
      chain,
      days,
    });
    if (error) {
      logger.warn({ productId, error: error.message }, 'Failed to get anomalies');
      return null;
    }
    return data;
  }

  async runScrape(chain: string, adminToken: string): Promise<ScrapeJobResponse | null> {
    const { data, error } = await scraperClient.post<ScrapeJobResponse>(
      `/scrape/run/${chain}`,
      {},
      {
        'X-Admin-Token': adminToken,
      }
    );
    if (error) {
      logger.error({ chain, error: error.message }, 'Scrape run failed');
      return null;
    }
    return data;
  }

  async getScrapeStatus(): Promise<Record<string, unknown>> {
    const { data, error } = await scraperClient.get<Record<string, unknown>>('/scrape');
    if (error) {
      logger.warn({ error: error.message }, 'Failed to get scrape status');
      return {};
    }
    return data || {};
  }
}