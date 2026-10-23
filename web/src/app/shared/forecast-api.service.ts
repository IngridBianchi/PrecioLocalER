import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, forkJoin, map, of } from 'rxjs';

import { environment } from '../../environments/environment';
import {
  CombinedForecast,
  FORECAST_HORIZON,
  ForecastPoint,
  ForecastSource,
  ModelForecast,
  combineForecasts
} from '../shared/models';

interface PythonPoint {
  date: string;
  predicted_price?: number | string | null;
  conf_low?: number | string | null;
  conf_high?: number | string | null;
}

interface PythonForecastPayload {
  model?: string;
  requested_model?: string | null;
  degraded_reason?: string | null;
  n_points?: number;
  points?: PythonPoint[];
}

interface DotnetPoint {
  date: string;
  predictedPrice?: number | string | null;
  confLow?: number | string | null;
  confHigh?: number | string | null;
}

interface DotnetForecastPayload {
  model?: string;
  degradedReason?: string | null;
  nPoints?: number;
  points?: DotnetPoint[];
}

export interface ForecastParams {
  productId: number;
  chain: string | null;
  referencePrice: number | null;
}

const num = (value: unknown): number | null => {
  if (value === null || value === undefined || value === '') {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const normalize = (
  point: { date: string },
  predicted: unknown,
  low: unknown,
  high: unknown
): ForecastPoint | null => {
  const predictedPrice = num(predicted);
  if (!point?.date || predictedPrice === null) {
    return null;
  }
  return {
    date: point.date,
    predictedPrice,
    confLow: num(low),
    confHigh: num(high)
  };
};

const describeError = (error: unknown): string => {
  if (error instanceof HttpErrorResponse) {
    if (error.status === 0) {
      return 'el servicio no respondió';
    }
    const body = error.error as { detail?: string; title?: string } | string | null;
    const detail = typeof body === 'object' && body ? (body.detail ?? body.title) : null;
    return detail ? `${error.status}: ${detail}` : `HTTP ${error.status}`;
  }
  return 'error inesperado';
};

const unavailable = (source: ForecastSource, model: string, reason: string): ModelForecast => ({
  source,
  model,
  degradedReason: reason,
  nPoints: 0,
  points: []
});

/**
 * forecasts del ADR-0006: Holt-Winters (Python) + SsaForecaster (.NET).
 *
 * Los dos servicios son independientes: que uno esté caído no puede dejar sin
 * banda al usuario, así que cada request degrada por separado y la banda se
 * arma con el modelo que respondió, marcado como débil.
 */
@Injectable({ providedIn: 'root' })
export class ForecastApiService {
  private readonly http = inject(HttpClient);

  forecast({ productId, chain, referencePrice }: ForecastParams): Observable<CombinedForecast> {
    return forkJoin({
      python: this.fromPython(productId, chain),
      dotnet: this.fromDotnet(productId, chain)
    }).pipe(map((models) => combineForecasts(models.python, models.dotnet, referencePrice)));
  }

  private fromPython(productId: number, chain: string | null): Observable<ModelForecast> {
    return this.http
      .get<PythonForecastPayload>(`${environment.apiBaseUrl}/ml/forecast/${productId}`, {
        params: { horizon: FORECAST_HORIZON, ...(chain ? { chain } : {}) }
      })
      .pipe(
        map((payload) => ({
          source: 'python' as const,
          model: payload.model ?? 'sarimax',
          requestedModel: payload.requested_model ?? null,
          degradedReason: payload.degraded_reason ?? null,
          nPoints: payload.n_points ?? 0,
          points: (payload.points ?? [])
            .map((point) => normalize(point, point.predicted_price, point.conf_low, point.conf_high))
            .filter((point): point is ForecastPoint => point !== null)
        })),
        catchError((error: unknown) => of(unavailable('python', 'sarimax', describeError(error))))
      );
  }

  private fromDotnet(productId: number, chain: string | null): Observable<ModelForecast> {
    return this.http
      .get<DotnetForecastPayload>(`${environment.mlDotnetBaseUrl}/api/forecast/${productId}`, {
        params: { horizon: FORECAST_HORIZON, ...(chain ? { chain } : {}) }
      })
      .pipe(
        map((payload) => ({
          source: 'dotnet' as const,
          model: payload.model ?? 'mlnet',
          requestedModel: null,
          degradedReason: payload.degradedReason ?? null,
          nPoints: payload.nPoints ?? 0,
          points: (payload.points ?? [])
            .map((point) => normalize(point, point.predictedPrice, point.confLow, point.confHigh))
            .filter((point): point is ForecastPoint => point !== null)
        })),
        catchError((error: unknown) => of(unavailable('dotnet', 'mlnet', describeError(error))))
      );
  }
}
