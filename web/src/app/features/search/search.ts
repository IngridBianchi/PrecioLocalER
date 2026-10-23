import { Component, DestroyRef, inject, signal, computed, InjectionToken } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { ActivatedRoute, Params, Router, RouterLink } from '@angular/router';
import {
  catchError,
  debounceTime,
  distinctUntilChanged,
  finalize,
  map,
  of
} from 'rxjs';

import { ChainPickerComponent } from '../../shared/chain-picker';
import { ProductSearchPage, ScraperApiService } from '../../shared/scraper-api.service';
import { Chain, Product, money, promoLabel, shortDate } from '../../shared/models';

export const SEARCH_DEBOUNCE_TIME = new InjectionToken<number>('SEARCH_DEBOUNCE_TIME', {
  providedIn: 'root',
  factory: () => 300
});

export const SEARCH_PAGE_SIZE = 10;
export const MAX_SEARCH_PAGE_SIZE = 50;

const clampInt = (value: number, min: number, max: number): number => {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.min(max, Math.max(min, Math.floor(value)));
};

@Component({
  selector: 'app-search',
  imports: [ReactiveFormsModule, RouterLink, ChainPickerComponent],
  templateUrl: './search.html',
  styleUrl: './search.css'
})
export class SearchComponent {
  private readonly api = inject(ScraperApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly debounceTime = inject(SEARCH_DEBOUNCE_TIME);

  readonly query = new FormControl('', { nonNullable: true });
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly chains = signal<Chain[]>([]);
  readonly chain = signal<string | null>(null);
  readonly results = signal<Product[]>([]);
  readonly total = signal(0);
  readonly limit = signal(SEARCH_PAGE_SIZE);
  readonly offset = signal(0);

  readonly totalResults = computed(() => this.total());
  readonly filteredResults = computed(() => this.results());
  readonly displayedResults = computed(() => this.results());
  readonly page = computed(() => Math.floor(this.offset() / this.limit()) + 1);
  readonly pages = computed(() => Math.max(1, Math.ceil(this.total() / this.limit())));

  /** Contadores de la página visible; el total global lo aporta el backend. */
  readonly chainCounts = computed(() => {
    const counts = new Map<string, number>();
    for (const product of this.results()) {
      counts.set(product.chain, (counts.get(product.chain) ?? 0) + 1);
    }
    return counts;
  });

  readonly totalChainCount = computed(() => this.total());

  constructor() {
    this.api
      .chains()
      .pipe(
        catchError(() => of([] as Chain[])),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe((chains) => this.chains.set(chains));

    // La URL es la fuente de verdad: permite compartir una búsqueda y que el
    // botón Atrás restaure término, cadena y página.
    this.route.queryParams.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((params) => {
      this.applyParams(params);
    });

    this.query.valueChanges
      .pipe(
        debounceTime(this.debounceTime),
        map((value) => value.trim()),
        distinctUntilChanged(),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe((term) => {
        if (term === (this.route.snapshot.queryParamMap.get('q') ?? '')) {
          return;
        }
        this.navigate({ q: term || null, offset: null });
      });

    if (typeof window !== 'undefined') {
      (window as any).__PRECIOLOCAL_TEST__ = {
        searchComponent: this,
        setQuery: (value: string) => this.query.setValue(value),
        triggerSearch: () => this.triggerSearch()
      };
    }
  }

  triggerSearch(): void {
    this.navigate({ q: this.query.value.trim() || null, offset: null });
  }

  setChain(slug: string | null): void {
    this.navigate({ chain: slug, offset: null });
  }

  goToPage(pageNumber: number): void {
    const target = Math.max(0, (Math.floor(pageNumber) - 1) * this.limit());
    if (target === this.offset()) {
      return;
    }
    this.navigate({ offset: target === 0 ? null : target });
  }

  previousPage(): void {
    this.goToPage(this.page() - 1);
  }

  nextPage(): void {
    this.goToPage(this.page() + 1);
  }

  private applyParams(params: Params): void {
    const q = typeof params['q'] === 'string' ? params['q'] : '';
    const requestedLimit = Number(params['limit']);
    const limit = clampInt(
      Number.isFinite(requestedLimit) && requestedLimit > 0 ? requestedLimit : SEARCH_PAGE_SIZE,
      1,
      MAX_SEARCH_PAGE_SIZE
    );
    const requestedOffset = Number(params['offset']);
    const offset =
      Number.isFinite(requestedOffset) && requestedOffset > 0 ? Math.floor(requestedOffset) : 0;
    const chain = typeof params['chain'] === 'string' && params['chain'] ? params['chain'] : null;

    this.query.setValue(q, { emitEvent: false });
    this.limit.set(limit);
    this.offset.set(offset);
    this.chain.set(chain);
    this.fetch(q, chain, limit, offset);
  }

  private fetch(q: string, chain: string | null, limit: number, offset: number): void {
    const term = q.trim();
    if (term.length < 2) {
      this.results.set([]);
      this.total.set(0);
      this.error.set(null);
      this.loading.set(false);
      return;
    }

    this.loading.set(true);
    this.error.set(null);
    this.api
      .searchPage({ q: term, chain: chain ?? undefined, limit, offset })
      .pipe(
        catchError(() => {
          this.error.set(
            'No se pudo consultar el catálogo. Verificá que el scraper esté corriendo.'
          );
          const empty: ProductSearchPage = { products: [], total: 0 };
          return of(empty);
        }),
        finalize(() => this.loading.set(false)),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe((page) => {
        this.results.set(page.products);
        this.total.set(page.total);
      });
  }

  private navigate(queryParams: Record<string, string | number | null>): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams,
      queryParamsHandling: 'merge',
      replaceUrl: true
    });
  }

  readonly formatPrice = money;
  readonly formatPromo = promoLabel;
  readonly formatDate = shortDate;
}
