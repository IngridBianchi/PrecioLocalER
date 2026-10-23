import { Component, OnInit, OnDestroy, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Subscription, catchError, forkJoin, of, take } from 'rxjs';

import { GraphqlApiService } from '../../core/graphql-api.service';
import { AuthService } from '../../core/auth.service';
import { NotificationService } from '../../core/notification.service';
import { money, shortDate, directionLabel } from '../../shared/models';

interface InflationCategory {
  category: { id: string; name: string; slug: string };
  inflation: number;
  productCount: number;
  avgPriceChange: number;
}

interface TopMover {
  product: { id: string; name: string };
  chain: { name: string; slug: string };
  priceChange: number;
  priceChangePct: number;
  currentPrice: string;
  previousPrice: string;
}

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.css',
})
export class DashboardComponent implements OnInit, OnDestroy {
  private readonly graphql = inject(GraphqlApiService);
  private readonly auth = inject(AuthService);
  private readonly notifications = inject(NotificationService);

  loading = signal(true);
  error = signal<string | null>(null);

  inflationReport = signal<any>(null);
  topGainers = signal<TopMover[]>([]);
  topLosers = signal<TopMover[]>([]);

  private subs: Subscription[] = [];

  ngOnInit(): void {
    this.loadDashboardData();
    this.subscribeToAlerts();
  }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }

  loadDashboardData(): void {
    this.loading.set(true);
    this.error.set(null);

    // `take(1)` es necesario: Apollo `valueChanges` es un watch que no
    // completa, así que sin él `forkJoin` esperaría para siempre y el
    // skeleton nunca desaparecería. El estado de carga pasa a ser real
    // (antes dependía de un `setTimeout` fijo de 500 ms).
    forkJoin({
      inflation: this.graphql.getInflationReport('30d').pipe(
        take(1),
        catchError((err) => {
          this.error.set('Error cargando inflación: ' + (err?.message ?? err));
          return of(null);
        })
      ),
      movers: this.graphql.getTopMovers('30d', 5).pipe(
        take(1),
        catchError((err) => {
          this.error.set('Error cargando movimientos: ' + (err?.message ?? err));
          return of(null);
        })
      )
    }).subscribe(({ inflation, movers }) => {
      if (inflation) {
        this.inflationReport.set(inflation);
      }
      if (movers) {
        this.topGainers.set(movers.gainers ?? []);
        this.topLosers.set(movers.losers ?? []);
      }
      this.loading.set(false);
    });
  }

  private subscribeToAlerts(): void {
    if (!this.auth.isAuthenticated) return;

    const sub = this.graphql.onAlertTriggered().subscribe({
      next: (event) => this.showAlertNotification(event),
      error: (err) => console.error('WS alert error:', err),
    });
    this.subs.push(sub);
  }

  private showAlertNotification(event: any): void {
    this.notifications.warning(
      'Alerta de precio',
      `${event.alert.product.name} ${event.alert.direction === 'BELOW' ? 'bajó' : 'subió'} a ${this.money(event.price)} (objetivo: ${this.money(event.alert.targetPrice)})`
    );
  }

  money = money;
  shortDate = shortDate;
  directionLabel = directionLabel;

  min(a: number, b: number): number {
    return Math.min(a, b);
  }

  max(a: number, b: number): number {
    return Math.max(a, b);
  }

  get overallInflation(): number {
    return this.inflationReport()?.overallInflation || 0;
  }

  /**
   * El gateway todavía devuelve un reporte vacío (`overallInflation: 0` y
   * `categories: []`) como stub. Sin esta guarda, ese cero se pintaba como
   * una inflación real de 0,0 %; cuando no hay datos, la pantalla lo dice.
   */
  readonly hasInflationData = computed(() => {
    const report = this.inflationReport();
    if (!report) {
      return false;
    }
    return (report.categories?.length ?? 0) > 0 || (report.overallInflation ?? 0) !== 0;
  });

  get categories(): InflationCategory[] {
    return this.inflationReport()?.categories || [];
  }

  get formattedDate(): string {
    return this.inflationReport()?.generatedAt
      ? shortDate(this.inflationReport().generatedAt)
      : '—';
  }
}