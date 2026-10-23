export interface Product {
  id: number;
  name: string;
  normalized_name: string;
  brand: string | null;
  category: string | null;
  category_slug: string | null;
  size_quantity: number | null;
  size_unit: string;
  ean: string | null;
  chain: string;
  chain_name: string;
  price: string;
  promo_type: string | null;
  captured_at: string;
}

export interface ProductDetail {
  id: number;
  name: string;
  normalized_name: string;
  brand: string | null;
  category: string | null;
  category_slug: string | null;
  size_quantity: number | null;
  size_unit: string;
  ean: string | null;
  prices: PricePoint[];
}

export interface PricePoint {
  id: number;
  product_id: number;
  chain: string;
  chain_id: number;
  store_id: number | null;
  price: string;
  promo_type: string | null;
  captured_at: string;
  scraped_at: string;
  hash: string;
}

export const SPANS = ['7d', '30d', '90d'] as const;
export type Span = (typeof SPANS)[number];

export interface Chain {
  id: number;
  slug: string;
  name: string;
}

export type ForecastSource = 'python' | 'dotnet';
export type ForecastDirection = 'up' | 'down' | 'flat';

/** Punto de forecast normalizado: Python y .NET usan la misma forma. */
export interface ForecastPoint {
  date: string;
  predictedPrice: number;
  confLow: number | null;
  confHigh: number | null;
}

export interface ModelForecast {
  source: ForecastSource;
  model: string;
  requestedModel?: string | null;
  degradedReason: string | null;
  nPoints: number;
  points: ForecastPoint[];
}

export interface ForecastBandPoint {
  date: string;
  low: number;
  high: number;
  python: number | null;
  dotnet: number | null;
  /** null si falta un modelo, no hay precio de referencia o discrepan. */
  direction: ForecastDirection | null;
  /** true si este día se calculó con un solo modelo. */
  weak: boolean;
}

export interface CombinedForecast {
  points: ForecastBandPoint[];
  direction: ForecastDirection | null;
  /** true si la banda se construyó con menos de dos modelos. */
  weak: boolean;
  models: string[];
  degraded: string[];
  /** 0..1: proporción de días en que los dos modelos coinciden. */
  agreementRatio: number;
}

export const FORECAST_HORIZON = 7;

/**
 * Tolerancia para considerar "flat" un movimiento: 1% del precio de
 * referencia. Sin ella, el ruido del redondeo a centavos genera señales
 * de "sube" en días donde el precio no se movió.
 */
export const FLAT_TOLERANCE = 0.01;

export const forecastDirection = (
  reference: number | null,
  predicted: number | null,
  tolerance = FLAT_TOLERANCE
): ForecastDirection | null => {
  if (reference === null || predicted === null) {
    return null;
  }
  if (!Number.isFinite(reference) || !Number.isFinite(predicted)) {
    return null;
  }
  const threshold = Math.abs(reference) * tolerance;
  const delta = predicted - reference;
  if (delta > threshold) {
    return 'up';
  }
  return delta < -threshold ? 'down' : 'flat';
};

/**
 * Banda de predicción del ADR-0006.
 *
 * La banda es el mínimo/máximo entre Holt-Winters (Python) y SsaForecaster (.NET):
 * el precio esperado no es un número, es un rango. La dirección solo se publica
 * si los dos modelos coinciden; si discrepan, es `null` y la UI lo dice, en
 * vez de mostrar una flecha que un modelo contradice.
 *
 * Si solo llega un modelo (el otro degradó o está caído) la banda se arma con
 * el que vino, marcada `weak`, y `degraded` explica por qué.
 */
export const combineForecasts = (
  python: ModelForecast | null,
  dotnet: ModelForecast | null,
  referencePrice: number | null = null
): CombinedForecast => {
  const incoming: [ForecastSource, ModelForecast | null][] = [
    ['python', python],
    ['dotnet', dotnet]
  ];
  const active = incoming.filter((entry): entry is [ForecastSource, ModelForecast] =>
    Boolean(entry[1] && entry[1].points.length > 0)
  );
  const degraded = incoming.flatMap(([, forecast]) =>
    forecast?.degradedReason ? [`${forecast.model}: ${forecast.degradedReason}`] : []
  );

  const byDate = new Map<string, ForecastBandPoint>();
  for (const [source, forecast] of active) {
    for (const point of forecast.points) {
      const existing = byDate.get(point.date);
      if (!existing) {
        byDate.set(point.date, {
          date: point.date,
          low: point.predictedPrice,
          high: point.predictedPrice,
          python: null,
          dotnet: null,
          direction: null,
          weak: true
        });
      }
      const slot = byDate.get(point.date)!;
      slot.low = Math.min(slot.low, point.predictedPrice);
      slot.high = Math.max(slot.high, point.predictedPrice);
      slot[source] = point.predictedPrice;
    }
  }

  const points = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));
  let agreed = 0;
  for (const point of points) {
    const votes = [forecastDirection(referencePrice, point.python), forecastDirection(referencePrice, point.dotnet)].filter(
      (vote): vote is ForecastDirection => vote !== null
    );
    point.weak = votes.length < 2;
    point.direction = point.weak ? null : votes[0] === votes[1] ? votes[0] : null;
    if (point.direction !== null) {
      agreed += 1;
    }
  }

  return {
    points,
    direction: points.at(-1)?.direction ?? null,
    weak: active.length < 2,
    models: active.map(([, forecast]) => forecast.model),
    degraded,
    agreementRatio: points.length === 0 ? 0 : agreed / points.length
  };
};

export const ars = new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS' });

export const money = (value: string | number | null | undefined): string =>
  value === null || value === undefined || value === '' ? '—' : ars.format(Number(value));

export const shortDate = (iso: string): string => {
  const d = new Date(iso);
  return d.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' });
};

/**
 * Etiqueta de un día `yyyy-MM-dd` sin pasar por `new Date`: parsear esa fecha
 * como UTC y formatearla en hora local (UTC-3) la correría un día.
 */
export const dayLabel = (day: string): string => `${day.slice(8, 10)}/${day.slice(5, 7)}`;

export const promoLabel = (promo: string | null): string | null => {
  if (!promo) return null;
  const m = promo.match(/^(\d+)%_off$/);
  return m ? `${m[1]}% OFF` : promo;
};

export const directionLabel = (direction: ForecastDirection | null): string =>
  direction === null ? 'Sin consenso' : direction === 'up' ? 'Sube' : direction === 'down' ? 'Baja' : 'Estable';