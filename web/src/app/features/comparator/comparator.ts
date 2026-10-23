import { Component, OnInit, OnDestroy, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  Subject,
  Subscription,
  debounceTime,
  switchMap,
  of,
  catchError
} from 'rxjs';

import { GraphqlApiService } from '../../core/graphql-api.service';
import { Chain, money, shortDate, promoLabel } from '../../shared/models';

interface ProductForCompare {
  id: string;
  name: string;
  normalizedName: string;
  brand: { name: string } | null;
  sizeQuantity: number | null;
  sizeUnit: string;
  prices: Array<{
    // `getProduct` devuelve `chain` como string (slug) y `compare` como
    // objeto; el componente tiene que tolerar ambas formas.
    chain: string | { id: string | null; name: string; slug: string };
    sku: { id: string; externalId: string } | null;
    price: string;
    promoType: string | null;
    capturedAt: string;
  }>;
}

@Component({
  selector: 'app-comparator',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './comparator.html',
  styleUrl: './comparator.css',
})
export class ComparatorComponent implements OnInit, OnDestroy {
  private readonly graphql = inject(GraphqlApiService);

  loading = signal(false);
  error = signal<string | null>(null);
  searchQuery = signal('');
  selectedProducts = signal<ProductForCompare[]>([]);
  searchResults = signal<any[]>([]);
  showSearchResults = signal(false);
  chains = signal<Chain[]>([]);

  private searchSub?: Subscription;
  private chainsSub?: Subscription;
  private readonly searchTerm$ = new Subject<string>();

  ngOnInit(): void {
    this.setupSearch();
    this.loadChains();
  }

  ngOnDestroy(): void {
    this.searchSub?.unsubscribe();
    this.chainsSub?.unsubscribe();
    this.searchTerm$.complete();
  }

  private loadChains(): void {
    this.chainsSub = this.graphql.getChains().subscribe({
      next: (chains) => this.chains.set(chains ?? []),
      error: () => this.chains.set([])
    });
  }

  /**
   * Búsqueda con debounce y cancelación: sin esto cada tecla disparaba una
   * petición al gateway (rate limit) y un response viejo podía pintarse después
   * del término nuevo. `switchMap` descarta la petición anterior.
   *
   * No se usa `distinctUntilChanged`: al añadir un producto se limpian los
   * resultados y volver a buscar el mismo término tiene que disparar la
   * consulta de nuevo.
   */
  private setupSearch(): void {
    this.searchSub = this.searchTerm$
      .pipe(
        debounceTime(300),
        switchMap((term) => {
          if (term.length < 2) {
            this.searchResults.set([]);
            this.showSearchResults.set(false);
            return of({ products: [] });
          }

          return this.graphql.searchProducts(term, undefined, 10).pipe(
            catchError((err) => {
              this.error.set('Error en búsqueda: ' + (err?.message ?? err));
              this.showSearchResults.set(false);
              return of({ products: [] });
            })
          );
        })
      )
      .subscribe((result: any) => {
        const products = result?.products ?? [];
        this.searchResults.set(products);
        this.showSearchResults.set(products.length > 0);
      });
  }

  // Handler for search input blur
  onSearchBlur(): void {
    setTimeout(() => this.showSearchResults.set(false), 200);
  }

  // Computed for promo check
  readonly hasAnyPromo = computed(() => 
    this.selectedProducts().some(p => p.prices?.some(pp => pp.promoType))
  );

  onSearchInput(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.searchQuery.set(value);
    this.searchTerm$.next(value);
  }

  addProduct(product: any): void {
    if (this.selectedProducts().length >= 3) return;
    if (this.selectedProducts().some(p => p.id === product.id)) return;

    this.graphql.getProduct(product.id).subscribe({
      next: (fullProduct) => {
        this.selectedProducts.update(p => [...p, fullProduct]);
        this.searchQuery.set('');
        this.searchResults.set([]);
        this.showSearchResults.set(false);
      },
      error: (err) => this.error.set('Error cargando producto: ' + err.message),
    });
  }

  removeProduct(productId: string): void {
    this.selectedProducts.update(p => p.filter(prod => prod.id !== productId));
  }

  compare(): void {
    if (this.selectedProducts().length < 2) return;

    const ids = this.selectedProducts().map(p => p.id);
    this.loading.set(true);
    this.error.set(null);

    this.graphql.compare(ids).subscribe({
      next: (results) => {
        // La query `compare` devuelve `{ product, prices }`; hay que aplanarlo
        // porque la tabla lee `product.prices`. Mapear sólo `r.product` dejaba
        // `prices` indefinido y el panel de comparación se ocultaba después de
        // comparar.
        this.selectedProducts.set(
          results.map((r: any) => ({ ...r.product, prices: r.prices ?? [] }))
        );
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set('Error comparando: ' + err.message);
        this.loading.set(false);
      },
    });
  }

  clearAll(): void {
    this.selectedProducts.set([]);
  }

  get hasComparison(): boolean {
    return this.selectedProducts().length >= 2 && 
           this.selectedProducts().some(p => p.prices && p.prices.length > 0);
  }

  get allChains(): string[] {
    const slugs = new Set<string>();
    this.selectedProducts().forEach((p) =>
      p.prices?.forEach((pp) => {
        const slug = typeof pp.chain === 'string' ? pp.chain : pp.chain?.slug;
        if (slug) {
          slugs.add(slug);
        }
      })
    );
    return Array.from(slugs).sort();
  }

  /** Muestra el nombre comercial; si no está en el catálogo, vuelve al slug. */
  chainLabel(slug: string): string {
    return this.chains().find((c) => c.slug === slug)?.name ?? slug;
  }

  getPriceForChain(product: ProductForCompare, chainSlug: string): { price: string; promo: string | null; capturedAt: string } | null {
    const match = product.prices?.find((pp) => {
      const slug = typeof pp.chain === 'string' ? pp.chain : pp.chain?.slug;
      return slug === chainSlug;
    });
    return match ? { price: match.price, promo: match.promoType, capturedAt: match.capturedAt } : null;
  }

  money = money;
  shortDate = shortDate;
  promoLabel = promoLabel;
}