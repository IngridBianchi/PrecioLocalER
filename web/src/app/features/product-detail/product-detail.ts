import { Component, DestroyRef, ElementRef, computed, effect, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import Chart from 'chart.js/auto';
import { Subject, catchError, combineLatest, distinctUntilChanged, map, of, startWith, switchMap, tap } from 'rxjs';

import { ChainPickerComponent } from '../../shared/chain-picker';
import { ForecastApiService } from '../../shared/forecast-api.service';
import { ScraperApiService } from '../../shared/scraper-api.service';
import {
  ars,
  Chain,
  CombinedForecast,
  dayLabel,
  directionLabel,
  ForecastDirection,
  money,
  PricePoint,
  ProductDetail,
  promoLabel,
  shortDate,
  Span,
  SPANS
} from '../../shared/models';

/** Último precio de mercado: promedio del día más reciente del histórico. */
const referenceOf = (history: PricePoint[]): number | null => {
  if (history.length === 0) {
    return null;
  }
  const lastDay = history[0].captured_at.slice(0, 10);
  const sameDay = history.filter((point) => point.captured_at.slice(0, 10) === lastDay);
  const total = sameDay.reduce((acc, point) => acc + Number(point.price), 0);
  return total / sameDay.length;
};

const newestFirst = (points: PricePoint[]): PricePoint[] =>
  [...points].sort((a, b) => b.captured_at.localeCompare(a.captured_at));

const spanFromUrl = (snapshot: ActivatedRoute['snapshot']): Span => {
  const value = snapshot.queryParamMap.get('span') ?? '30d';
  return (SPANS as readonly string[]).includes(value) ? (value as Span) : '30d';
};

@Component({
  selector: 'app-product-detail',
  imports: [RouterLink, ChainPickerComponent],
  templateUrl: './product-detail.html',
  styleUrl: './product-detail.css'
})
export class ProductDetailComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly api = inject(ScraperApiService);
  private readonly forecasts = inject(ForecastApiService);
  private readonly destroyRef = inject(DestroyRef);
  private chart?: Chart;
  private readonly reload$ = new Subject<void>();

  /**
   * `viewChild` resuelve a `ElementRef` para nodos DOM, no al elemento pelado:
   * hay que desenrollar `nativeElement` antes de pasárselo a Chart.js. Con el
   * `ElementRef` crudo, `acquireContext` devolvía null y Chart.js abortaba con
   * "can't acquire context from the given item", dejando el gráfico en blanco
   * sin error visible en la consola de producción (solo un `console.error`).
   */
  private readonly canvas = viewChild<ElementRef<HTMLCanvasElement>>('chart');

  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly span = signal<Span>(spanFromUrl(this.route.snapshot));
  readonly chain = signal<string | null>(this.route.snapshot.queryParamMap.get('chain'));
  readonly chains = signal<Chain[]>([]);
  readonly product = signal<ProductDetail | null>(null);
  readonly history = signal<PricePoint[]>([]);
  readonly latestPrice = signal<string | null>(null);
  readonly latestPromo = signal<string | null>(null);
  readonly latestCapture = signal<string | null>(null);
  readonly forecast = signal<CombinedForecast | null>(null);
  readonly forecastLoading = signal(true);
  readonly lastPoint = computed(() => this.forecast()?.points.at(-1) ?? null);

  readonly spans: Span[] = [...SPANS];
  readonly formatPrice = money;
  readonly formatPromo = promoLabel;
  readonly formatDate = (iso: string): string => shortDate(iso);
  readonly formatDay = dayLabel;
  readonly formatDirection = directionLabel;
  readonly formatAgreement = (ratio: number): string => `${Math.round(ratio * 100)}%`;
  readonly formatFullDate = (iso: string): string =>
    new Date(iso).toLocaleDateString('es-AR', {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit'
    });

  constructor() {
    this.api
      .chains()
      .pipe(
        catchError(() => of([] as Chain[])),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe((chains) => this.chains.set(chains));

    // `route.params` re-emite en toda navegación, incluso en las que solo
    // cambian query params (que es lo que hace `syncUrl`). Filtrar por id
    // evita recargar el producto dos veces por cada cambio de cadena.
    combineLatest([
      this.route.params.pipe(
        map((params) => Number(params['id'])),
        distinctUntilChanged()
      ),
      this.reload$.pipe(startWith(undefined))
    ])
      .pipe(
        switchMap(([id]) => {
          const span = this.span();
          const chain = this.chain();
          this.loading.set(true);
          this.error.set(null);
          this.forecastLoading.set(true);
          this.forecast.set(null);
          return combineLatest([this.api.detail(id), this.api.history(id, span, chain)]).pipe(
            switchMap(([detail, history]) =>
              this.forecasts
                .forecast({ productId: id, chain, referencePrice: referenceOf(history) })
                .pipe(
                  map((combined) => ({ detail, history, combined })),
                  // ForecastApiService ya degrada por modelo; esto evita que un
                  // fallo inesperado tumbe la página con el histórico ya cargado.
                  catchError(() => of({ detail, history, combined: null }))
                )
            ),
            tap(() => {
              this.loading.set(false);
              this.forecastLoading.set(false);
            }),
            catchError(() => {
              this.error.set('No se pudo cargar el producto solicitado.');
              this.loading.set(false);
              this.forecastLoading.set(false);
              return of(null);
            })
          );
        }),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe((result) => {
        if (!result) {
          return;
        }
        const { detail, history, combined } = result;
        const scoped = history.filter((point) => !this.chain() || point.chain === this.chain());
        const latest = newestFirst(scoped)[0] ?? newestFirst(detail.prices)[0];
        this.product.set(detail);
        this.history.set(scoped);
        this.forecast.set(combined);
        this.latestPrice.set(latest?.price ?? null);
        this.latestPromo.set(latest?.promo_type ?? null);
        this.latestCapture.set(latest?.captured_at ?? null);
      });

    effect((onCleanup) => {
      this.span();
      this.chain();
      this.history();
      this.forecast();
      const canvas = this.canvas()?.nativeElement;
      if (canvas) {
        this.renderChart(canvas);
        onCleanup(() => this.chart?.destroy());
      }
    });
  }

  setSpan(span: Span): void {
    this.span.set(span);
    this.reload$.next();
    this.syncUrl();
  }

  setChain(slug: string | null): void {
    this.chain.set(slug);
    this.reload$.next();
    this.syncUrl();
  }

  private syncUrl(): void {
    // `queryParams: { chain: null }` con `merge` NO borra el parámetro: la URL
    // seguía diciendo ?chain=carrefour después de volver a "Todas", así que
    // recargar o compartir el link reaplicaba un filtro que la pantalla ya no
    // tenía. Se escribe el estado completo (sin `merge`) y `chain` solo cuando
    // hay cadena elegida: la URL queda siendo espejo exacto de lo que se ve.
    const chain = this.chain();
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { span: this.span(), ...(chain ? { chain } : {}) },
      replaceUrl: true
    });
  }

  readonly directionClass = (direction: ForecastDirection | null, extra = ''): string =>
    `direction ${direction ?? 'flat'}${extra ? ` ${extra}` : ''}`;

  private renderChart(canvas: HTMLCanvasElement): void {
    const history = this.history();
    const band = this.forecast()?.points ?? [];

    // Un punto por día: el histórico crudo trae varias capturas por día y
    // mezcla eso con la granularidad diaria del forecast.
    const observed = new Map<string, number>();
    for (const point of [...history].sort((a, b) => a.captured_at.localeCompare(b.captured_at))) {
      observed.set(point.captured_at.slice(0, 10), Number(point.price));
    }

    const dates = [...new Set([...observed.keys(), ...band.map((p) => p.date)])].sort();
    const align = (source: Map<string, number | null>): (number | null)[] =>
      dates.map((day) => source.get(day) ?? null);

    const datasets: {
      label: string;
      data: (number | null)[];
      borderColor: string;
      backgroundColor: string;
      fill?: '+1' | false;
      pointRadius: number;
      tension: number;
      order: number;
      spanGaps: boolean;
      borderDash?: number[];
    }[] = [];

    if (band.length > 0) {
      // `fill: '+1'` une este dataset con el siguiente del array, así que la
      // banda se declara como dos datasets contiguos (borde superior e inferior).
      datasets.push(
        {
          label: 'Banda de predicción',
          data: align(new Map(band.map((p) => [p.date, p.high]))),
          borderColor: 'transparent',
          backgroundColor: 'rgba(11, 107, 58, 0.14)',
          fill: '+1',
          pointRadius: 0,
          tension: 0.25,
          order: 1,
          spanGaps: true
        },
        {
          label: '',
          data: align(new Map(band.map((p) => [p.date, p.low]))),
          borderColor: 'transparent',
          backgroundColor: 'transparent',
          pointRadius: 0,
          tension: 0.25,
          order: 1,
          spanGaps: true
        }
      );
    }

    if (observed.size > 0) {
      datasets.push({
        label: 'Precio observado',
        data: align(new Map([...observed].map(([day, value]) => [day, value as number | null]))),
        borderColor: '#0b6b3a',
        backgroundColor: 'rgba(11, 107, 58, 0.08)',
        fill: false,
        pointRadius: 3,
        tension: 0.25,
        order: 2,
        spanGaps: false
      });
    }

    const combined = this.forecast();
    if (combined && !combined.weak) {
      datasets.push(
        {
          label: 'Holt-Winters (Python)',
          data: align(new Map(band.map((p) => [p.date, p.python]))),
          borderColor: '#1f6feb',
          backgroundColor: 'transparent',
          pointRadius: 0,
          tension: 0.25,
          order: 3,
          spanGaps: true
        },
        {
          label: 'SsaForecaster (.NET)',
          data: align(new Map(band.map((p) => [p.date, p.dotnet]))),
          borderColor: '#8250df',
          backgroundColor: 'transparent',
          pointRadius: 0,
          tension: 0.25,
          order: 3,
          spanGaps: true,
          borderDash: [5, 4]
        }
      );
    }

    if (datasets.length === 0) {
      return;
    }

    this.chart?.destroy();
    this.chart = new Chart(canvas, {
      type: 'line',
      data: {
        labels: dates.map(dayLabel),
        datasets
      },
      options: {
        responsive: true,
        interaction: { intersect: false, mode: 'index' },
        scales: {
          y: {
            ticks: {
              callback: (value) => ars.format(Number(value))
            }
          }
        },
        plugins: {
          legend: { display: band.length > 0, position: 'bottom' },
          tooltip: {
            callbacks: {
              label: (ctx) => ` ${ctx.dataset.label || 'Banda'}: ${ars.format(Number(ctx.parsed.y))}`
            }
          }
        }
      }
    });
  }
}
