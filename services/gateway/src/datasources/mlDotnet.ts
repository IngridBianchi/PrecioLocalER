import { mlDotnetClient } from '../utils/httpClient.js';
import { logger } from '../utils/logger.js';

// ============================================================
// Types
// ============================================================

export interface ForecastResponse {
  product_id: string;
  chain?: string;
  model: string;
  horizon_days: number;
  predictions: Array<{
    date: string;
    predicted_price: number;
    conf_low?: number;
    conf_high?: number;
  }>;
  metrics?: {
    mape: number;
    rmse: number;
    mae: number;
    n_points: number;
  };
}

export interface ComparisonResponse {
  model: string;
  language: string;
  mape: number;
  rmse: number;
  mae: number;
  num_points: number;
  trained_at: string;
}

export interface PricesReportResponse {
  chain?: string;
  products: Array<{
    product_id: string;
    product_name: string;
    chain: string;
    price: string;
    captured_at: string;
  }>;
}

export interface InflationReportResponse {
  period: string;
  span: string;
  categories: Array<{
    categoryName: string;
    categorySlug: string;
    productCount: number;
    inflation: number;
    avgPriceChange: number;
  }>;
  overallInflation: number;
  generatedAt: string;
}

export interface TopMoversResponse {
  period: string;
  span: string;
  gainers: Array<{
    productId: number;
    productName: string;
    chainId: number;
    chainName: string;
    currentPrice: number;
    previousPrice: number;
    priceChange: number;
    priceChangePct: number;
  }>;
  losers: Array<{
    productId: number;
    productName: string;
    chainId: number;
    chainName: string;
    currentPrice: number;
    previousPrice: number;
    priceChange: number;
    priceChangePct: number;
  }>;
  generatedAt: string;
}

export interface HealthResponse {
  status: string;
  service: string;
  mklAvailable: boolean;
}

// ============================================================
// ML.NET DataSource
// ============================================================

export class MlDotnetDataSource {
  async health(): Promise<HealthResponse> {
    const { data, error } = await mlDotnetClient.get<HealthResponse>('/health');
    if (error) {
      logger.error({ error: error.message }, 'ML.NET health check failed');
      return { status: 'unhealthy', service: 'ml-dotnet', mklAvailable: false };
    }
    return data || { status: 'unknown', service: 'ml-dotnet', mklAvailable: false };
  }

  async getForecast(productId: string, chain?: string, horizon = 7): Promise<ForecastResponse | null> {
    const { data, error } = await mlDotnetClient.get<ForecastResponse>(`/api/forecast/${productId}`, {
      chain,
      horizon,
    });
    if (error) {
      logger.warn({ productId, error: error.message }, 'Failed to get ML.NET forecast');
      return null;
    }
    return data;
  }

  async getComparison(productId: string, chainId: string): Promise<ComparisonResponse[]> {
    const { data, error } = await mlDotnetClient.get<ComparisonResponse[]>(
      `/api/forecast/${productId}/comparison`,
      { chainId }
    );
    if (error) {
      logger.warn({ productId, error: error.message }, 'Failed to get comparison');
      return [];
    }
    return data || [];
  }

  async getPricesReport(chain?: string, query?: string, limit = 50): Promise<PricesReportResponse> {
    const { data, error } = await mlDotnetClient.get<PricesReportResponse>('/api/reports/prices', {
      chain,
      q: query,
      limit,
    });
    if (error) {
      logger.warn({ error: error.message }, 'Failed to get prices report');
      return { products: [] };
    }
    return data || { products: [] };
  }

  async getInflationReport(span = '30d'): Promise<InflationReportResponse | null> {
    const { data, error } = await mlDotnetClient.get<InflationReportResponse>('/api/reports/inflation', {
      span,
    });
    if (error) {
      logger.warn({ error: error.message }, 'Failed to get inflation report');
      return null;
    }
    return data || null;
  }

  async getTopMovers(span = '30d', top = 20): Promise<TopMoversResponse | null> {
    const { data, error } = await mlDotnetClient.get<TopMoversResponse>('/api/reports/movers', {
      span,
      top,
    });
    if (error) {
      logger.warn({ error: error.message }, 'Failed to get top movers');
      return null;
    }
    return data || null;
  }
}