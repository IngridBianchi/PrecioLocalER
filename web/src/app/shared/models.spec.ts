import {
  combineForecasts,
  dayLabel,
  forecastDirection,
  ForecastPoint,
  ModelForecast
} from './models';

const point = (
  date: string,
  price: number,
  confLow: number | null = null,
  confHigh: number | null = null
): ForecastPoint => ({ date, predictedPrice: price, confLow, confHigh });

const model = (
  source: 'python' | 'dotnet',
  name: string,
  points: ForecastPoint[],
  degradedReason: string | null = null
): ModelForecast => ({
  source,
  model: name,
  requestedModel: null,
  degradedReason,
  nPoints: points.length,
  points
});

// Referencia 100 => umbral de estabilidad 1. El día 1 coincide (suben ambos),
// el día 2 discrepan (uno sube, el otro baja).
const PYTHON = model('python', 'sarimax', [point('2026-10-01', 105), point('2026-10-02', 110)]);
const DOTNET = model('dotnet', 'mlnet', [point('2026-10-01', 108), point('2026-10-02', 90)]);

describe('forecastDirection', () => {
  it('distingue suba, baja y estabilidad según el precio de referencia', () => {
    expect(forecastDirection(100, 120)).toBe('up');
    expect(forecastDirection(100, 80)).toBe('down');
    expect(forecastDirection(100, 100)).toBe('flat');
  });

  it('trata como estable un movimiento menor al 1%', () => {
    expect(forecastDirection(1000, 1005)).toBe('flat');
    expect(forecastDirection(1000, 1015)).toBe('up');
  });

  it('devuelve null sin referencia o con valores no finitos', () => {
    expect(forecastDirection(null, 120)).toBeNull();
    expect(forecastDirection(100, null)).toBeNull();
    expect(forecastDirection(100, Number.NaN)).toBeNull();
  });
});

describe('combineForecasts', () => {
  it('arma la banda con el mínimo y el máximo de los dos modelos', () => {
    const combined = combineForecasts(PYTHON, DOTNET, 100);
    expect(combined.points.map((p) => [p.low, p.high])).toEqual([
      [105, 108],
      [90, 110]
    ]);
    expect(combined.points[0].python).toBe(105);
    expect(combined.points[0].dotnet).toBe(108);
    expect(combined.weak).toBeFalse();
    expect(combined.models).toEqual(['sarimax', 'mlnet']);
  });

  it('solo publica dirección en los días donde los dos modelos coinciden', () => {
    const combined = combineForecasts(PYTHON, DOTNET, 100);
    expect(combined.points[0].direction).toBe('up');
    expect(combined.points[1].direction).toBeNull();
    expect(combined.agreementRatio).toBe(0.5);
  });

  it('toma la dirección del último día cuando hay coincidencia completa', () => {
    const agree = model('dotnet', 'mlnet', [point('2026-10-01', 102), point('2026-10-02', 118)]);
    const combined = combineForecasts(PYTHON, agree, 100);
    expect(combined.direction).toBe('up');
    expect(combined.agreementRatio).toBe(1);
  });

  it('degrada a un solo modelo y lo marca como débil', () => {
    const combined = combineForecasts(PYTHON, null, 100);
    expect(combined.points.length).toBe(2);
    expect(combined.weak).toBeTrue();
    expect(combined.direction).toBeNull();
    expect(combined.points.every((p) => p.weak)).toBeTrue();
    expect(combined.agreementRatio).toBe(0);
  });

  it('reporta el motivo de degradación de cada modelo', () => {
    const broken = model('dotnet', 'mlnet', [], 'serie demasiado corta');
    const combined = combineForecasts(PYTHON, broken, 100);
    expect(combined.degraded).toEqual(['mlnet: serie demasiado corta']);
    expect(combined.models).toEqual(['sarimax']);
  });

  it('ignora el modelo que llegó sin puntos y conserva el rango del otro', () => {
    const combined = combineForecasts(PYTHON, model('dotnet', 'mlnet', []), 100);
    expect(combined.points.map((p) => [p.low, p.high])).toEqual([
      [105, 105],
      [110, 110]
    ]);
  });

  it('no inventa dirección sin precio de referencia', () => {
    const combined = combineForecasts(PYTHON, DOTNET, null);
    expect(combined.points.every((p) => p.direction === null)).toBeTrue();
  });

  it('une fechas que solo existen en uno de los modelos', () => {
    const staggered = model('dotnet', 'mlnet', [point('2026-10-03', 120)]);
    const combined = combineForecasts(PYTHON, staggered, 100);
    expect(combined.points.map((p) => p.date)).toEqual(['2026-10-01', '2026-10-02', '2026-10-03']);
    expect(combined.points[2].python).toBeNull();
    expect(combined.points[2].dotnet).toBe(120);
  });

  it('devuelve una banda vacía si no llegó ningún modelo', () => {
    const combined = combineForecasts(null, null, 100);
    expect(combined.points).toEqual([]);
    expect(combined.direction).toBeNull();
    expect(combined.weak).toBeTrue();
    expect(combined.agreementRatio).toBe(0);
  });
});

describe('dayLabel', () => {
  it('formatea yyyy-MM-dd sin corrimiento por zona horaria', () => {
    expect(dayLabel('2026-10-01')).toBe('01/10');
    expect(dayLabel('2026-01-31')).toBe('31/01');
  });
});
