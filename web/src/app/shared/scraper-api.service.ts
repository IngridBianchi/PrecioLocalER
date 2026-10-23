import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';

import { environment } from '../../environments/environment';
import { Chain, PricePoint, Product, ProductDetail, Span } from '../shared/models';
import { GraphqlApiService } from '../core/graphql-api.service';

export interface SearchParams {
  q: string;
  chain?: string;
  limit?: number;
  offset?: number;
}

export interface ProductSearchPage {
  products: Product[];
  total: number;
}

const mapProduct = (p: any): Product => ({
  id: p.id,
  name: p.name,
  normalized_name: p.normalizedName,
  brand: p.brand,
  category: p.category,
  category_slug: p.categorySlug,
  size_quantity: p.sizeQuantity,
  size_unit: p.sizeUnit,
  ean: p.ean,
  chain: p.chain,
  chain_name: p.chainName,
  price: p.price,
  promo_type: p.promoType,
  captured_at: p.capturedAt,
});

@Injectable({ providedIn: 'root' })
export class ScraperApiService {
  private readonly graphql = inject(GraphqlApiService);

  /** Búsqueda paginada: conserva el `total` que el backend ya devuelve. */
  searchPage(params: SearchParams): Observable<ProductSearchPage> {
    return this.graphql.searchProducts(params.q, params.chain, params.limit, params.offset).pipe(
      map((result: any) => ({
        products: (result.products ?? []).map(mapProduct),
        total: result.total ?? result.products?.length ?? 0,
      }))
    );
  }

  search(params: SearchParams): Observable<Product[]> {
    return this.searchPage(params).pipe(map((page) => page.products));
  }

  chains(): Observable<Chain[]> {
    return this.graphql.getChains().pipe(
      map((chains: any[]) => chains.map(c => ({
        id: c.id,
        slug: c.slug,
        name: c.name,
      })))
    );
  }

  detail(productId: number): Observable<ProductDetail> {
    return this.graphql.getProduct(String(productId)).pipe(
      map((p: any) => ({
        id: p.id,
        name: p.name,
        normalized_name: p.normalizedName,
        brand: p.brand?.name || null,
        category: p.category?.name || null,
        category_slug: p.category?.slug || null,
        size_quantity: p.sizeQuantity,
        size_unit: p.sizeUnit,
        ean: p.ean,
        prices: p.prices?.map((pp: any) => ({
          id: pp.id,
          product_id: p.id,
          chain: pp.chain,
          chain_id: pp.chainId,
          sku_id: pp.skuId,
          store_id: pp.storeId,
          price: pp.price,
          promo_type: pp.promoType,
          captured_at: pp.capturedAt,
          scraped_at: pp.scrapedAt,
          hash: pp.hash,
        })) || [],
      }))
    );
  }

  history(productId: number, span: Span, chain?: string | null): Observable<PricePoint[]> {
    return this.graphql.getPriceHistory(String(productId), span, chain ?? undefined).pipe(
      map((points: any[]) => points.map(pp => ({
        id: pp.id,
        product_id: pp.productId,
        chain: pp.chain,
        chain_id: pp.chainId,
        store_id: pp.storeId,
        price: pp.price,
        promo_type: pp.promoType,
        captured_at: pp.capturedAt,
        scraped_at: pp.scrapedAt,
        hash: pp.hash,
      })))
    );
  }
}