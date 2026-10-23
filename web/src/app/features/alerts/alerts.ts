import { Component, OnInit, OnDestroy, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';

import { GraphqlApiService } from '../../core/graphql-api.service';
import { AuthService } from '../../core/auth.service';
import { NotificationService } from '../../core/notification.service';
import { money, shortDate, promoLabel } from '../../shared/models';

interface Alert {
  id: string;
  userId: string;
  product: {
    id: string;
    name: string;
    normalizedName: string;
    brand: { name: string } | null;
    sizeQuantity: number | null;
    sizeUnit: string;
  };
  direction: 'BELOW' | 'ABOVE';
  targetPrice: string;
  active: boolean;
  lastTriggeredAt: string | null;
  createdAt: string;
}

interface AlertEvent {
  id: string;
  alertId: string;
  pricePointId: string;
  price: string;
  triggeredAt: string;
  alert: {
    id: string;
    product: { id: string; name: string };
    direction: 'BELOW' | 'ABOVE';
    targetPrice: string;
  };
}

@Component({
  selector: 'app-alerts',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './alerts.html',
  styleUrl: './alerts.css',
})
export class AlertsComponent implements OnInit, OnDestroy {
  private readonly graphql = inject(GraphqlApiService);
  protected readonly auth = inject(AuthService);
  private readonly notifications = inject(NotificationService);

  loading = signal(false);
  error = signal<string | null>(null);
  creating = signal(false);

  alerts = signal<Alert[]>([]);
  alertEvents = signal<AlertEvent[]>([]);

  // Create alert form
  showCreateForm = signal(false);
  searchProduct = signal('');
  searchResults = signal<any[]>([]);
  showSearchResults = signal(false);
  readonly activeIndex = signal(-1);
  selectedProduct: any = null;
  direction: 'BELOW' | 'ABOVE' = 'BELOW';
  targetPrice = '';

  private subs: Subscription[] = [];

  // Computed for active alerts count
  readonly activeAlertsCount = computed(() => this.alerts().filter(a => a.active).length);

  // Handler for search input blur
  onSearchBlur(): void {
    setTimeout(() => {
      this.showSearchResults.set(false);
      this.activeIndex.set(-1);
    }, 200);
  }

  /** Navegación por teclado del autocompletado (combobox pattern). */
  onSearchKeydown(event: KeyboardEvent): void {
    if (!this.showSearchResults() || this.searchResults().length === 0) {
      return;
    }
    const last = this.searchResults().length - 1;
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        this.activeIndex.update((i) => (i >= last ? 0 : i + 1));
        break;
      case 'ArrowUp':
        event.preventDefault();
        this.activeIndex.update((i) => (i <= 0 ? last : i - 1));
        break;
      case 'Enter': {
        const selected = this.searchResults()[this.activeIndex()];
        if (selected) {
          event.preventDefault();
          this.selectProduct(selected);
        }
        break;
      }
      case 'Escape':
        this.showSearchResults.set(false);
        this.activeIndex.set(-1);
        break;
    }
  }

  ngOnInit(): void {
    if (!this.auth.isAuthenticated) {
      this.error.set('Inicia sesión para gestionar alertas');
      return;
    }
    this.loadAlerts();
    this.loadAlertEvents();
    this.subscribeToLiveAlerts();
  }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }

  private loadAlerts(): void {
    this.loading.set(true);
    this.graphql.getMyAlerts().subscribe({
      next: (data) => {
        this.alerts.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set('Error cargando alertas: ' + err.message);
        this.loading.set(false);
      },
    });
  }

  private loadAlertEvents(): void {
    this.graphql.getAlertEvents(50).subscribe({
      next: (data) => this.alertEvents.set(data),
      error: (err) => console.error('Error loading alert events:', err),
    });
  }

  private subscribeToLiveAlerts(): void {
    const sub = this.graphql.onAlertTriggered().subscribe({
      next: (event) => {
        this.alertEvents.update(events => [event, ...events.slice(0, 49)]);
        this.showLiveNotification(event);
      },
      error: (err) => console.error('WS alert error:', err),
    });
    this.subs.push(sub);
  }

  private showLiveNotification(event: AlertEvent): void {
    this.notifications.warning(
      'Alerta disparada',
      `${event.alert.product.name} ${event.alert.direction === 'BELOW' ? 'bajó' : 'subió'} a ${this.money(event.price)} (objetivo: ${this.money(event.alert.targetPrice)})`,
      10000
    );
  }

  toggleCreateForm(): void {
    this.showCreateForm.update(v => !v);
    if (!this.showCreateForm()) {
      this.resetForm();
    }
  }

  private resetForm(): void {
    this.searchProduct.set('');
    this.searchResults.set([]);
    this.showSearchResults.set(false);
    this.selectedProduct = null;
    this.direction = 'BELOW';
    this.targetPrice = '';
  }

  onProductSearch(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.searchProduct.set(value);

    if (value.length < 2) {
      this.searchResults.set([]);
      this.showSearchResults.set(false);
      this.activeIndex.set(-1);
      return;
    }

    this.graphql.searchProducts(value, undefined, 10).subscribe({
      next: (result) => {
        const products = result.products ?? [];
        this.searchResults.set(products);
        this.showSearchResults.set(products.length > 0);
        this.activeIndex.set(-1);
      },
      error: (err) => console.error('Search error:', err),
    });
  }

  selectProduct(product: any): void {
    this.selectedProduct = product;
    this.searchProduct.set(product.name);
    this.searchResults.set([]);
    this.showSearchResults.set(false);
    this.activeIndex.set(-1);
  }

  createAlert(): void {
    if (!this.selectedProduct || !this.targetPrice) return;

    this.creating.set(true);
    this.error.set(null);

    this.graphql.createAlert({
      productId: this.selectedProduct.id,
      direction: this.direction,
      targetPrice: this.targetPrice,
    }).subscribe({
      next: (alert) => {
        this.alerts.update(a => [alert, ...a]);
        this.resetForm();
        this.showCreateForm.set(false);
        this.creating.set(false);
      },
      error: (err) => {
        this.error.set('Error creando alerta: ' + err.message);
        this.creating.set(false);
      },
    });
  }

  toggleAlert(alert: Alert): void {
    this.graphql.toggleAlert(alert.id, !alert.active).subscribe({
      next: (updated) => {
        this.alerts.update(a => a.map(a => a.id === alert.id ? updated : a));
      },
      error: (err) => this.error.set('Error: ' + err.message),
    });
  }

  deleteAlert(alert: Alert): void {
    if (!confirm('¿Eliminar esta alerta?')) return;

    this.graphql.deleteAlert(alert.id).subscribe({
      next: () => {
        this.alerts.update(a => a.filter(a => a.id !== alert.id));
      },
      error: (err) => this.error.set('Error: ' + err.message),
    });
  }

  get directionLabel(): string {
    return this.direction === 'BELOW' ? 'Cuando baje de' : 'Cuando suba de';
  }

  money = money;
  shortDate = shortDate;
  promoLabel = promoLabel;
}