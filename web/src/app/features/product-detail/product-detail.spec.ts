import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import Chart from 'chart.js/auto';
import { of } from 'rxjs';

import { ForecastApiService } from '../../shared/forecast-api.service';
import {
  Chain,
  ModelForecast,
  PricePoint,
  ProductDetail,
  combineForecasts
} from '../../shared/models';
import { ScraperApiService } from '../../shared/scraper-api.service';
import { ProductDetailComponent } from './product-detail';

const CHAINS: Chain[] = [
  { id: 1, slug: 'coto', name: 'Coto' },
  { id: 2, slug: 'disco', name: 'Disco' }
];

const point = (chain: string, price: string, day: string): PricePoint => ({
  id: 1,
  product_id: 7,
  chain,
  chain_id: 1,
  store_id: null,
  price,
  promo_type: null,
  captured_at: `${day}T12:00:00Z`,
  scraped_at: `${day}T12:05:00Z`,
  hash: chain.padEnd(64, '0')
});

const DETAIL: ProductDetail = {
  id: 7,
  name: 'Aceite de Girasol x 900 cc.',
  normalized_name: 'aceite de girasol 900cc',
  brand: 'La Anónima',
  category: 'Aceites',
  category_slug: 'aceites',
  size_quantity: 0.9,
  size_unit: 'cc',
  ean: null,
  prices: [point('coto', '5000', '2026-09-20'), point('disco', '5200', '2026-09-20')]
};

const PY_MODEL: ModelForecast = {
  source: 'python',
  model: 'sarimax',
  requestedModel: 'sarimax',
  degradedReason: null,
  nPoints: 1,
  points: [{ date: '2026-10-01', predictedPrice: 5100, confLow: 5000, confHigh: 5200 }]
};

const NET_MODEL: ModelForecast = {
  source: 'dotnet',
  model: 'mlnet',
  requestedModel: null,
  degradedReason: null,
  nPoints: 1,
  points: [{ date: '2026-10-01', predictedPrice: 5300, confLow: 5150, confHigh: 5450 }]
};

const COMBINED = combineForecasts(PY_MODEL, NET_MODEL, 5100);
const WEAK_COMBINED = combineForecasts(
  PY_MODEL,
  { ...NET_MODEL, nPoints: 0, points: [], degradedReason: '404: sin histórico' },
  5100
);

describe('ProductDetailComponent', () => {
  let fixture: ComponentFixture<ProductDetailComponent>;
  let component: ProductDetailComponent;
  let router: Router;
  let scraper: jasmine.SpyObj<ScraperApiService>;
  let forecasts: jasmine.SpyObj<ForecastApiService>;

  const routeStub = {
    params: of({ id: '7' }),
    snapshot: { queryParamMap: convertToParamMap({ span: '30d' }) }
  };

  beforeEach(async () => {
    scraper = jasmine.createSpyObj<ScraperApiService>('ScraperApiService', [
      'chains',
      'detail',
      'history'
    ]);
    forecasts = jasmine.createSpyObj<ForecastApiService>('ForecastApiService', ['forecast']);

    scraper.chains.and.returnValue(of(CHAINS));
    scraper.detail.and.returnValue(of(DETAIL));
    scraper.history.and.returnValue(
      of([point('coto', '5000', '2026-09-20'), point('disco', '5200', '2026-09-20')])
    );
    forecasts.forecast.and.returnValue(of(COMBINED));

    await TestBed.configureTestingModule({
      imports: [ProductDetailComponent],
      providers: [
        provideRouter([]),
        { provide: ActivatedRoute, useValue: routeStub },
        { provide: ScraperApiService, useValue: scraper },
        { provide: ForecastApiService, useValue: forecasts }
      ]
    }).compileComponents();

    router = TestBed.inject(Router);
    // `syncUrl` no es lo que se testea acá y sin router real la navegación
    // relativa no tiene a dónde ir.
    spyOn(router, 'navigate').and.resolveTo(true);
  });

  /**
   * Se crea dentro de cada test: el componente dispara la carga en el
   * constructor, así que los spies tienen que estar configurados antes.
   */
  const create = (): void => {
    fixture = TestBed.createComponent(ProductDetailComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  };

  it('carga el detalle y arma la banda con los dos modelos', fakeAsync(() => {
    create();
    tick();

    expect(component.product()?.id).toBe(7);
    expect(component.loading()).toBeFalse();
    expect(component.forecastLoading()).toBeFalse();

    const combined = component.forecast();
    expect(combined).not.toBeNull();
    expect(combined!.models).toEqual(['sarimax', 'mlnet']);
    expect(combined!.weak).toBeFalse();
    expect(combined!.points[0].low).toBe(5100);
    expect(combined!.points[0].high).toBe(5300);
  }));

  it('pide histórico y forecast filtrados por la cadena elegida', fakeAsync(() => {
    create();
    tick();

    scraper.history.calls.reset();
    forecasts.forecast.calls.reset();
    scraper.history.and.returnValue(of([point('coto', '5000', '2026-09-20')]));

    component.setChain('coto');
    tick();

    expect(scraper.history).toHaveBeenCalledWith(7, '30d', 'coto');
    expect(forecasts.forecast).toHaveBeenCalledWith(
      jasmine.objectContaining({ productId: 7, chain: 'coto' })
    );
    expect(component.chain()).toBe('coto');
    expect(component.history().every((p) => p.chain === 'coto')).toBeTrue();
  }));

  it('refleja la cadena activa en la URL para poder compartir el enlace', fakeAsync(() => {
    create();
    tick();

    component.setChain('disco');
    tick();

    expect(router.navigate).toHaveBeenCalledWith(
      [],
      jasmine.objectContaining({ queryParams: { span: '30d', chain: 'disco' } })
    );
  }));

  it('saca el chain de la URL al volver a "Todas"', fakeAsync(() => {
    create();
    tick();

    component.setChain(null);
    tick();

    // Con `queryParamsHandling: 'merge'` y `chain: null` el parámetro NO se
    // borraba: el link seguía diciendo ?chain=... y al recargar reaplicaba un
    // filtro que la pantalla ya no tenía.
    expect(router.navigate).toHaveBeenCalledWith(
      [],
      jasmine.objectContaining({ queryParams: { span: '30d' } })
    );
  }));

  it('crea el chart sobre el canvas real, no sobre el ElementRef', fakeAsync(() => {
    // Regresión: `viewChild('chart')` devuelve un ElementRef para nodos DOM.
    // Pasárselo crudo a Chart.js hacía que acquireContext devolviera null y el
    // gráfico quedara en blanco con un solo console.error en producción.
    scraper.history.and.returnValue(
      of([point('coto', '5000', '2026-09-20'), point('coto', '5050', '2026-09-21')])
    );

    create();
    tick();
    // El canvas vive dentro de un @if del template: sin detectChanges la vista
    // no existe y `viewChild` sigue en undefined.
    fixture.detectChanges();
    tick();

    const chart = (component as unknown as { chart?: Chart }).chart;
    expect(chart).toBeDefined();
    expect(chart?.canvas).toBeInstanceOf(HTMLCanvasElement);
    expect(fixture.nativeElement.querySelector('canvas')).toBe(chart?.canvas);
  }));

  it('degrada a un solo modelo sin perder el histórico', fakeAsync(() => {
    forecasts.forecast.and.returnValue(of(WEAK_COMBINED));
    scraper.history.and.returnValue(of([point('coto', '5000', '2026-09-20')]));

    create();
    tick();

    expect(component.forecast()?.weak).toBeTrue();
    expect(component.forecast()?.degraded).toEqual(['mlnet: 404: sin histórico']);
    expect(component.history().length).toBe(1);
    expect(component.error()).toBeNull();
  }));
});
