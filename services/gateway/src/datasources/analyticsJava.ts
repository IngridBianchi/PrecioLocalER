import { analyticsJavaClient } from '../utils/httpClient.js';
import { logger } from '../utils/logger.js';

// ============================================================
// Types
// ============================================================

export interface AlertEvent {
  id: string;
  alert_id: string;
  price_point_id: string;
  price: string;
  triggered_at: string;
}

export interface AlertResponse {
  id: string;
  userId: string;
  productId: string;
  direction: 'BELOW' | 'ABOVE';
  targetPrice: string;
  active: boolean;
  lastTriggeredAt: string | null;
  createdAt: string;
}

export interface HealthResponse {
  status: string;
  service: string;
  checks: {
    postgres: boolean;
    mongodb: boolean;
  };
}

// ============================================================
// Analytics Java DataSource
// ============================================================

export class AnalyticsJavaDataSource {
  async health(): Promise<HealthResponse> {
    const { data, error } = await analyticsJavaClient.get<HealthResponse>('/health');
    if (error) {
      logger.error({ error: error.message }, 'Analytics Java health check failed');
      return { status: 'unhealthy', service: 'analytics-java', checks: { postgres: false, mongodb: false } };
    }
    return data || { status: 'unknown', service: 'analytics-java', checks: { postgres: false, mongodb: false } };
  }

  async ready(): Promise<{ status: string; checks: { postgres: boolean; mongodb: boolean } }> {
    const { data, error } = await analyticsJavaClient.get<{ status: string; checks: { postgres: boolean; mongodb: boolean } }>('/ready');
    if (error) {
      return { status: 'not_ready', checks: { postgres: false, mongodb: false } };
    }
    return data || { status: 'not_ready', checks: { postgres: false, mongodb: false } };
  }

  async getAlertEvents(userId: string): Promise<AlertEvent[]> {
    const { data, error } = await analyticsJavaClient.get<AlertEvent[]>('/api/alert-events', {
      userId,
    });
    if (error) {
      logger.warn({ userId, error: error.message }, 'Failed to get alert events');
      return [];
    }
    return data || [];
  }

  async getAlerts(userId: string): Promise<AlertResponse[]> {
    const { data, error } = await analyticsJavaClient.get<AlertResponse[]>('/api/alerts', {
      userId,
    });
    if (error) {
      logger.warn({ userId, error: error.message }, 'Failed to get alerts');
      return [];
    }
    return data || [];
  }

  async createAlert(userId: string, productId: string, direction: 'BELOW' | 'ABOVE', targetPrice: string): Promise<AlertResponse | null> {
    const { data, error } = await analyticsJavaClient.post<AlertResponse>('/api/alerts', {
      userId,
      productId,
      direction,
      targetPrice,
    });
    if (error) {
      logger.warn({ userId, productId, error: error.message }, 'Failed to create alert');
      return null;
    }
    return data || null;
  }

  async toggleAlert(id: string, active: boolean): Promise<AlertResponse | null> {
    const { data, error } = await analyticsJavaClient.patch<AlertResponse>(`/api/alerts/${id}`, {
      active,
    });
    if (error) {
      logger.warn({ id, error: error.message }, 'Failed to toggle alert');
      return null;
    }
    return data || null;
  }

  async deleteAlert(id: string): Promise<boolean> {
    const { error } = await analyticsJavaClient.delete(`/api/alerts/${id}`);
    if (error) {
      logger.warn({ id, error: error.message }, 'Failed to delete alert');
      return false;
    }
    return true;
  }
}