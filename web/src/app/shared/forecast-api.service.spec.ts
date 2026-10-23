import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { environment } from '../../environments/environment';
import { ForecastApiService } from './forecast-api.service';
import { CombinedForecast } from './models';

const PY_URL = `${environment.apiBaseUrl}/ml/forecast/7`;
const NET_URL = `${environment.mlDotnetBaseUrl}/api/forecast/7`;

const pythonPayload = {
  product_id: 7,
  chain: 'coto',
  requested_model: 'sarimax',
  model: 'sarimax',
  degraded_reason: null,
  n_points: 2,
  points: [
    { date: '2026-10-01', predicted_price: 105, conf_low: 100, conf_high: 110 },
    { date: '2026-10-02', predicted_price: 110, conf_low: 104, conf_high: 116 }
  ]
};

const dotnetPayload = {
  productId: 7,
  chain: 'coto',
  model: 'mlnet',
  degradedReason: null,
  nPoints: 2,
  points: [
    { date: '2026-10-01', predictedPrice: 108, confLow: 102, confHigh: 114 },
    { date: '2026-10-02', predictedPrice: 90, confLow: 85, confHigh: 95 }
  ]
};

describe('ForecastApiService', () => {
  let service: ForecastApiService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()]
    });
    service = TestBed.inject(ForecastApiService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('consulta los dos servicios y arma la banda combinada', () => {
    let result: CombinedForecast | null = null;
    service.forecast({ productId: 7, chain: 'coto', referencePrice: 100 }).subscribe((r) => (result = r));

    const py = http.expectOne((r) => r.url === PY_URL);
    expect(py.request.params.get('chain')).toBe('coto');
    expect(py.request.params.get('horizon')).toBe('7');
    py.flush(pythonPayload);

    const net = http.expectOne((r) => r.url === NET_URL);
    expect(net.request.params.get('chain')).toBe('coto');
    net.flush(dotnetPayload);

    expect(result).not.toBeNull();
    const combined = result as unknown as CombinedForecast;
    expect(combined.models).toEqual(['sarimax', 'mlnet']);
    expect(combined.weak).toBeFalse();
    expect(combined.points.map((p) => [p.low, p.high])).toEqual([
      [105, 108],
      [90, 110]
    ]);
    expect(combined.points[0].direction).toBe('up');
    expect(combined.points[1].direction).toBeNull();
  });

  it('omite la cadena cuando no hay filtro', () => {
    service.forecast({ productId: 7, chain: null, referencePrice: 100 }).subscribe();

    const py = http.expectOne((r) => r.url === PY_URL);
    expect(py.request.params.has('chain')).toBeFalse();
    py.flush(pythonPayload);

    const net = http.expectOne((r) => r.url === NET_URL);
    expect(net.request.params.has('chain')).toBeFalse();
    net.flush(dotnetPayload);
  });

  it('mantiene la banda con un solo modelo si el servicio .NET falla', () => {
    let result: CombinedForecast | null = null;
    service.forecast({ productId: 7, chain: null, referencePrice: 100 }).subscribe((r) => (result = r));

    http.expectOne((r) => r.url === PY_URL).flush(pythonPayload);
    http
      .expectOne((r) => r.url === NET_URL)
      .flush({ detail: 'producto 7 no existe' }, { status: 404, statusText: 'Not Found' });

    const combined = result as unknown as CombinedForecast;
    expect(combined.weak).toBeTrue();
    expect(combined.models).toEqual(['sarimax']);
    expect(combined.points.length).toBe(2);
    expect(combined.degraded).toEqual(['mlnet: 404: producto 7 no existe']);
  });

  it('mantiene la banda con un solo modelo si el scraper falla', () => {
    let result: CombinedForecast | null = null;
    service.forecast({ productId: 7, chain: null, referencePrice: 100 }).subscribe((r) => (result = r));

    http.expectOne((r) => r.url === PY_URL).error(new ProgressEvent('error'), { status: 0 });
    http.expectOne((r) => r.url === NET_URL).flush(dotnetPayload);

    const combined = result as unknown as CombinedForecast;
    expect(combined.weak).toBeTrue();
    expect(combined.models).toEqual(['mlnet']);
    expect(combined.degraded).toEqual(['sarimax: el servicio no respondió']);
  });

  it('devuelve una banda vacía y explica ambos fallos si los dos servicios caen', () => {
    let result: CombinedForecast | null = null;
    service.forecast({ productId: 7, chain: null, referencePrice: 100 }).subscribe((r) => (result = r));

    http.expectOne((r) => r.url === PY_URL).flush({}, { status: 500, statusText: 'Error' });
    http.expectOne((r) => r.url === NET_URL).error(new ProgressEvent('error'), { status: 0 });

    const combined = result as unknown as CombinedForecast;
    expect(combined.points).toEqual([]);
    expect(combined.weak).toBeTrue();
    expect(combined.degraded.length).toBe(2);
  });

  it('reporta la degradación declarada por el propio backend', () => {
    let result: CombinedForecast | null = null;
    service.forecast({ productId: 7, chain: null, referencePrice: 100 }).subscribe((r) => (result = r));

    http.expectOne((r) => r.url === PY_URL).flush({ ...pythonPayload, degraded_reason: 'pocos datos' });
    http.expectOne((r) => r.url === NET_URL).flush(dotnetPayload);

    expect((result as unknown as CombinedForecast).degraded).toEqual(['sarimax: pocos datos']);
  });
});
