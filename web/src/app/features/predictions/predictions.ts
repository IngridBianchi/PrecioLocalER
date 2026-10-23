import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { switchMap, of, catchError, map } from 'rxjs';

import { GraphqlApiService } from '../../core/graphql-api.service';
import { 
  money, 
  shortDate, 
  dayLabel, 
  directionLabel,
  CombinedForecast,
  ForecastDirection,
  ForecastBandPoint
} from '../../shared/models';

interface ForecastData {
  python: any | null;
  dotnet: any | null;
  combined: CombinedForecast | null;
}

interface ChartPoint {
  x: number;
  y: number;
}

@Component({
  selector: 'app-predictions',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './predictions.html',
  styleUrl: './predictions.css',
})
export class PredictionsComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly graphql = inject(GraphqlApiService);

  loading = signal(false);
  error = signal<string | null>(null);

  productId = signal<string | null>(null);
  product = signal<any>(null);
  forecastData = signal<ForecastData | null>(null);

  // Chart constants
  readonly paddingLeft = 60;
  readonly paddingRight = 20;
  readonly paddingTop = 20;
  readonly paddingBottom = 40;
  readonly chartWidth = 800;
  readonly chartHeight = 300;

  ngOnInit(): void {
    this.route.paramMap.pipe(
      switchMap(params => {
        const id = params.get('id');
        if (!id) return of(null);
        this.productId.set(id);
        this.loading.set(true);
        this.error.set(null);

        // Load product details
        this.graphql.getProduct(id).subscribe({
          next: (p) => this.product.set(p),
          error: () => {},
        });

        // Load both forecasts
        return this.graphql.getForecast(id).pipe(
          switchMap(python => 
            this.graphql.getForecastComparison(id, 'la_anonima').pipe(
              catchError(() => of([])),
              map(dotnet => ({ python, dotnet }))
            )
          ),
          catchError(() => of({ python: null, dotnet: [] }))
        );
      })
    ).subscribe({
      next: (data) => {
        if (data) {
          this.forecastData.set({
            python: data.python,
            dotnet: data.dotnet[0] || null,
            combined: this.combineForecasts(data.python, data.dotnet[0] || null),
          });
        }
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set('Error cargando predicciones: ' + err.message);
        this.loading.set(false);
      },
    });
  }

  private combineForecasts(python: any, dotnet: any): CombinedForecast {
    if (!python && !dotnet) return { points: [], direction: null, weak: true, models: [], degraded: [], agreementRatio: 0 };

    const incoming: [string, any][] = [];
    if (python?.points?.length) incoming.push(['python', python]);
    if (dotnet?.points?.length) incoming.push(['dotnet', dotnet]);

    const active = incoming.filter(([, f]) => f?.points?.length > 0);
    const degraded = incoming.flatMap(([, f]) => 
      f?.degradedReason ? [`${f.model}: ${f.degradedReason}`] : []
    );

    const byDate = new Map<string, any>();
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
            weak: true,
          });
        }
        const slot = byDate.get(point.date)!;
        slot.low = Math.min(slot.low, point.predictedPrice);
        slot.high = Math.max(slot.high, point.predictedPrice);
        slot[source] = point.predictedPrice;
      }
    }

    const referencePrice = active[0]?.[1]?.points?.[0]?.predictedPrice || null;
    const points = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));
    let agreed = 0;
    for (const point of points) {
      const votes = [
        this.forecastDirection(referencePrice, point.python),
        this.forecastDirection(referencePrice, point.dotnet),
      ].filter((v): v is ForecastDirection => v !== null);
      
      point.weak = votes.length < 2;
      point.direction = point.weak ? null : votes[0] === votes[1] ? votes[0] : null;
      if (point.direction !== null) agreed += 1;
    }

    return {
      points,
      direction: points.at(-1)?.direction ?? null,
      weak: active.length < 2,
      models: active.map(([, f]) => f.model),
      degraded,
      agreementRatio: points.length === 0 ? 0 : agreed / points.length,
    };
  }

  private forecastDirection(reference: number | null, predicted: number | null): ForecastDirection | null {
    if (reference === null || predicted === null || !Number.isFinite(reference) || !Number.isFinite(predicted)) {
      return null;
    }
    const threshold = Math.abs(reference) * 0.01;
    const delta = predicted - reference;
    if (delta > threshold) return 'up';
    return delta < -threshold ? 'down' : 'flat';
  }

  // Computed signals for template
  readonly combined = computed(() => {
    const combined = this.forecastData()?.combined;
    if (!combined) {
      return { points: [], direction: null, weak: true, models: [], degraded: [], agreementRatio: 0 };
    }
    return combined;
  });
  readonly pythonForecast = computed(() => this.forecastData()?.python ?? { points: [], model: '', degradedReason: null });
  readonly dotnetForecast = computed(() => this.forecastData()?.dotnet ?? { points: [], model: '', degradedReason: null });
  readonly hasForecast = computed(() => this.combined().points.length > 0);

  // Helper for template - first point price
  readonly firstPointPrice = computed(() => {
    const points = this.combined().points;
    if (points.length === 0) return 0;
    return points[0].python ?? points[0].dotnet ?? 0;
  });

  // Chart helpers
  readonly yGridLines = computed(() => {
    if (!this.combined()?.points?.length) return [];
    const points = this.combined()!.points;
    const minPrice = Math.min(...points.map((p: ForecastBandPoint) => p.low));
    const maxPrice = Math.max(...points.map((p: ForecastBandPoint) => p.high));
    const step = (maxPrice - minPrice) / 4;
    return Array.from({ length: 5 }, (_, i) => {
      const price = minPrice + i * step;
      const y = this.paddingTop + (this.chartHeight - this.paddingTop - this.paddingBottom) * (1 - (price - minPrice) / (maxPrice - minPrice));
      return { y, price };
    });
  });

  readonly xGridLines = computed(() => {
    if (!this.combined()?.points?.length) return [];
    const count = this.combined()!.points.length;
    return this.combined()!.points.map((_, i) => {
      return this.paddingLeft + (this.chartWidth - this.paddingLeft - this.paddingRight) * (i / (count - 1));
    });
  });

  readonly bandPath = computed(() => {
    const combined = this.combined();
    if (!combined?.points?.length) return '';
    
    const points = combined.points;
    const minPrice = Math.min(...points.map(p => p.low));
    const maxPrice = Math.max(...points.map(p => p.high));
    const priceRange = maxPrice - minPrice || 1;
    
    let path = `M ${this.paddingLeft} ${this.chartHeight - this.paddingBottom}`;
    
    // Lower boundary
    points.forEach((point: ForecastBandPoint, i: number) => {
      const x = this.paddingLeft + (this.chartWidth - this.paddingLeft - this.paddingRight) * (i / (points.length - 1));
      const y = this.paddingTop + (this.chartHeight - this.paddingTop - this.paddingBottom) * (1 - (point.low - minPrice) / priceRange);
      if (i === 0) {
        path += ` L ${x} ${y}`;
      } else {
        path += ` L ${x} ${y}`;
      }
    });
    
    // Upper boundary (reverse)
    [...points].reverse().forEach((point: ForecastBandPoint, i: number) => {
      const x = this.paddingLeft + (this.chartWidth - this.paddingLeft - this.paddingRight) * (i / (points.length - 1));
      const y = this.paddingTop + (this.chartHeight - this.paddingTop - this.paddingBottom) * (1 - (point.high - minPrice) / priceRange);
      path += ` L ${x} ${y}`;
    });
    
    path += ' Z';
    return path;
  });

  readonly pythonPath = computed(() => {
    const python = this.pythonForecast();
    if (!python?.points?.length) return '';
    
    const points = python.points;
    const minPrice = Math.min(...this.combined()!.points.map(p => Math.min(p.low, p.high)));
    const maxPrice = Math.max(...this.combined()!.points.map(p => Math.max(p.low, p.high)));
    const priceRange = maxPrice - minPrice || 1;
    
    let path = '';
    points.forEach((point: { predictedPrice: number }, i: number) => {
      const x = this.paddingLeft + (this.chartWidth - this.paddingLeft - this.paddingRight) * (i / (points.length - 1));
      const y = this.paddingTop + (this.chartHeight - this.paddingTop - this.paddingBottom) * (1 - (point.predictedPrice - minPrice) / priceRange);
      if (i === 0) path = `M ${x} ${y}`;
      else path += ` L ${x} ${y}`;
    });
    return path;
  });

  readonly dotnetPath = computed(() => {
    const dotnet = this.dotnetForecast();
    if (!dotnet?.points?.length) return '';
    
    const points = dotnet.points;
    const minPrice = Math.min(...this.combined()!.points.map(p => Math.min(p.low, p.high)));
    const maxPrice = Math.max(...this.combined()!.points.map(p => Math.max(p.low, p.high)));
    const priceRange = maxPrice - minPrice || 1;
    
    let path = '';
    points.forEach((point: { predictedPrice: number }, i: number) => {
      const x = this.paddingLeft + (this.chartWidth - this.paddingLeft - this.paddingRight) * (i / (points.length - 1));
      const y = this.paddingTop + (this.chartHeight - this.paddingTop - this.paddingBottom) * (1 - (point.predictedPrice - minPrice) / priceRange);
      if (i === 0) path = `M ${x} ${y}`;
      else path += ` L ${x} ${y}`;
    });
    return path;
  });

  readonly pythonChartPoints = computed(() => {
    const python = this.pythonForecast();
    if (!python?.points?.length || !this.combined()) return [];
    
    const points = python.points;
    const minPrice = Math.min(...this.combined()!.points.map(p => Math.min(p.low, p.high)));
    const maxPrice = Math.max(...this.combined()!.points.map(p => Math.max(p.low, p.high)));
    const priceRange = maxPrice - minPrice || 1;
    
    return points.map((point: { predictedPrice: number }, i: number) => ({
      x: this.paddingLeft + (this.chartWidth - this.paddingLeft - this.paddingRight) * (i / (points.length - 1)),
      y: this.paddingTop + (this.chartHeight - this.paddingTop - this.paddingBottom) * (1 - (point.predictedPrice - minPrice) / priceRange),
    }));
  });

  readonly dotnetChartPoints = computed(() => {
    const dotnet = this.dotnetForecast();
    if (!dotnet?.points?.length || !this.combined()) return [];
    
    const points = dotnet.points;
    const minPrice = Math.min(...this.combined()!.points.map(p => Math.min(p.low, p.high)));
    const maxPrice = Math.max(...this.combined()!.points.map(p => Math.max(p.low, p.high)));
    const priceRange = maxPrice - minPrice || 1;
    
    return points.map((point: { predictedPrice: number }, i: number) => ({
      x: this.paddingLeft + (this.chartWidth - this.paddingLeft - this.paddingRight) * (i / (points.length - 1)),
      y: this.paddingTop + (this.chartHeight - this.paddingTop - this.paddingBottom) * (1 - (point.predictedPrice - minPrice) / priceRange),
    }));
  });

  getPythonPrice(date: string): number | null {
    const python = this.pythonForecast();
    if (!python?.points) return null;
    const point = python.points.find((p: { date: string; predictedPrice: number }) => p.date === date);
    return point?.predictedPrice ?? null;
  }

  getDotnetPrice(date: string): number | null {
    const dotnet = this.dotnetForecast();
    if (!dotnet?.points) return null;
    const point = dotnet.points.find((p: { date: string; predictedPrice: number }) => p.date === date);
    return point?.predictedPrice ?? null;
  }

  formatNumber(val: number | null | undefined): string {
    if (val === null || val === undefined) return '—';
    return val.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // Template helpers
  money = money;
  shortDate = shortDate;
  dayLabel = dayLabel;
  directionLabel = directionLabel;

  min(a: number, b: number): number {
    return Math.min(a, b);
  }

  max(a: number, b: number): number {
    return Math.max(a, b);
  }

  readonly minPrice = computed(() => {
    const c = this.combined();
    if (!c?.points?.length) return 0;
    return Math.min(...c.points.map(p => p.low));
  });

  readonly maxPrice = computed(() => {
    const c = this.combined();
    if (!c?.points?.length) return 0;
    return Math.max(...c.points.map(p => p.high));
  });
}