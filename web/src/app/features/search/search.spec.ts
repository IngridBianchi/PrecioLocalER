import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { ActivatedRoute, Params, Router, convertToParamMap, provideRouter } from '@angular/router';
import { BehaviorSubject, Observable, of } from 'rxjs';

import { Chain, Product } from '../../shared/models';
import { ProductSearchPage, ScraperApiService } from '../../shared/scraper-api.service';
import { SEARCH_DEBOUNCE_TIME, SearchComponent } from './search';

const CHAINS: Chain[] = [
  { id: 1, slug: 'coto', name: 'Coto' },
  { id: 2, slug: 'disco', name: 'Disco' }
];

const product = (id: number, chain: string, name = 'Aceite'): Product => ({
  id,
  name,
  normalized_name: name.toLowerCase(),
  brand: null,
  category: null,
  category_slug: null,
  size_quantity: 1,
  size_unit: 'L',
  ean: null,
  chain,
  chain_name: chain === 'coto' ? 'Coto' : 'Disco',
  price: '100',
  promo_type: null,
  captured_at: '2026-10-20T12:00:00Z'
});

describe('SearchComponent', () => {
  let fixture: ComponentFixture<SearchComponent>;
  let component: SearchComponent;
  let params$: BehaviorSubject<Params>;
  let router: Router;
  let scraper: jasmine.SpyObj<ScraperApiService>;
  let routeStub: {
    queryParams: Observable<Params>;
    snapshot: { queryParamMap: ReturnType<typeof convertToParamMap> };
  };

  beforeEach(async () => {
    params$ = new BehaviorSubject<Params>({});
    scraper = jasmine.createSpyObj<ScraperApiService>('ScraperApiService', [
      'chains',
      'searchPage',
      'search'
    ]);
    scraper.chains.and.returnValue(of(CHAINS));
    scraper.searchPage.and.returnValue(of({ products: [product(1, 'coto')], total: 1 }));

    routeStub = {
      queryParams: params$.asObservable(),
      snapshot: { queryParamMap: convertToParamMap(params$.value) }
    };

    await TestBed.configureTestingModule({
      imports: [SearchComponent],
      providers: [
        provideRouter([]),
        { provide: ActivatedRoute, useValue: routeStub },
        { provide: ScraperApiService, useValue: scraper },
        { provide: SEARCH_DEBOUNCE_TIME, useValue: 0 }
      ]
    }).compileComponents();

    router = TestBed.inject(Router);
    spyOn(router, 'navigate').and.callFake((_commands: unknown[], extras: any) => {
      const merged: Params = { ...params$.value, ...(extras?.queryParams ?? {}) };
      for (const key of Object.keys(merged)) {
        if (merged[key] === null || merged[key] === undefined) {
          delete merged[key];
        }
      }
      routeStub.snapshot.queryParamMap = convertToParamMap(merged);
      params$.next(merged);
      return Promise.resolve(true);
    });
  });

  /** Crea el componente dentro del test para que `debounceTime` viva en fakeAsync. */
  const create = (): void => {
    fixture = TestBed.createComponent(SearchComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  };

  it('carga las cadenas disponibles', () => {
    create();

    expect(scraper.chains).toHaveBeenCalled();
    expect(component.chains()).toEqual(CHAINS);
  });

  it('lee q, limit y offset de la URL y pide esa página', fakeAsync(() => {
    params$.next({ q: 'aceite', limit: '5', offset: '10' });
    create();
    tick();

    expect(scraper.searchPage).toHaveBeenCalledWith({
      q: 'aceite',
      chain: undefined,
      limit: 5,
      offset: 10
    });
    expect(component.offset()).toBe(10);
    expect(component.limit()).toBe(5);
    expect(component.displayedResults().length).toBe(1);
    expect(component.total()).toBe(1);
  }));

  it('normaliza offset negativo a 0 y limita limit a 50', fakeAsync(() => {
    params$.next({ q: 'aceite', limit: '999', offset: '-5' });
    create();
    tick();

    expect(scraper.searchPage).toHaveBeenCalledWith({
      q: 'aceite',
      chain: undefined,
      limit: 50,
      offset: 0
    });
  }));

  it('no consulta el backend con menos de dos caracteres', fakeAsync(() => {
    params$.next({ q: 'a' });
    create();
    tick();

    expect(scraper.searchPage).not.toHaveBeenCalled();
    expect(component.displayedResults()).toEqual([]);
  }));

  it('buscar desde el input actualiza la URL y vuelve a offset 0', fakeAsync(() => {
    params$.next({ q: 'aceite', offset: '20' });
    create();
    tick();

    component.query.setValue('leche');
    tick();

    expect(router.navigate).toHaveBeenCalledWith(
      [],
      jasmine.objectContaining({ queryParams: { q: 'leche', offset: null } })
    );
    expect(scraper.searchPage).toHaveBeenCalledWith({
      q: 'leche',
      chain: undefined,
      limit: 10,
      offset: 0
    });
  }));

  it('setChain filtra en el backend, resetea offset y conserva el término', fakeAsync(() => {
    params$.next({ q: 'aceite', offset: '10' });
    create();
    tick();

    component.setChain('coto');
    tick();

    expect(router.navigate).toHaveBeenCalledWith(
      [],
      jasmine.objectContaining({ queryParams: { chain: 'coto', offset: null } })
    );
    expect(scraper.searchPage).toHaveBeenCalledWith({
      q: 'aceite',
      chain: 'coto',
      limit: 10,
      offset: 0
    });
    expect(component.chain()).toBe('coto');
  }));

  it('cambiar de página pide el offset correspondiente', fakeAsync(() => {
    scraper.searchPage.and.returnValue(
      of<ProductSearchPage>({ products: [product(1, 'coto')], total: 35 })
    );
    params$.next({ q: 'aceite', limit: '10' });
    create();
    tick();

    component.goToPage(2);
    tick();

    expect(router.navigate).toHaveBeenCalledWith(
      [],
      jasmine.objectContaining({ queryParams: { offset: 10 } })
    );
    expect(scraper.searchPage).toHaveBeenCalledWith({
      q: 'aceite',
      chain: undefined,
      limit: 10,
      offset: 10
    });
    expect(component.page()).toBe(2);
    expect(component.pages()).toBe(4);
  }));

  it('calcula los contadores de cadena con la página visible', fakeAsync(() => {
    scraper.searchPage.and.returnValue(
      of<ProductSearchPage>({
        products: [product(1, 'coto'), product(2, 'disco'), product(3, 'coto')],
        total: 3
      })
    );
    params$.next({ q: 'aceite' });
    create();
    tick();

    expect(component.chainCounts().get('coto')).toBe(2);
    expect(component.chainCounts().get('disco')).toBe(1);
    expect(component.totalChainCount()).toBe(3);
  }));
});
