import { Component, inject, computed, HostListener, HostBinding } from '@angular/core';
import { CommonModule } from '@angular/common';

import { NotificationService, Notification } from './notification.service';

@Component({
  selector: 'app-notification-container',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="notification-container" [attr.aria-hidden]="notifications().length === 0" role="region" aria-live="polite" aria-atomic="true">
      @for (notification of notifications(); track notification.id) {
        <div class="notification" [class]="'notification-' + notification.type" role="alert" [attr.data-id]="notification.id">
          <div class="notification-content">
            <div class="notification-header">
              <strong class="notification-title">{{ notification.title }}</strong>
              <button class="notification-close" (click)="remove(notification.id)" aria-label="Cerrar notificación">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M4 4l8 8M12 4l-8 8"/>
                </svg>
              </button>
            </div>
            <div class="notification-message">{{ notification.message }}</div>
          </div>
        </div>
      }
    </div>
  `,
  styles: [`
    .notification-container {
      position: fixed;
      top: 1rem;
      right: 1rem;
      z-index: 10000;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      max-width: 360px;
      pointer-events: none;
    }

    .notification {
      pointer-events: auto;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow-lg);
      padding: var(--space-3) var(--space-4);
      animation: slideIn 0.3s ease-out;
    }

    @keyframes slideIn {
      from {
        opacity: 0;
        transform: translateX(100%);
      }
      to {
        opacity: 1;
        transform: translateX(0);
      }
    }

    .notification-content {
      display: flex;
      flex-direction: column;
      gap: var(--space-2);
    }

    .notification-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: var(--space-2);
    }

    .notification-title {
      font-weight: 600;
      font-size: var(--text-sm);
      color: var(--text);
    }

    .notification-message {
      font-size: var(--text-sm);
      color: var(--text);
      line-height: 1.4;
    }

    .notification-close {
      flex-shrink: 0;
      background: none;
      border: none;
      color: var(--muted);
      cursor: pointer;
      padding: 0.125rem;
      line-height: 1;
      border-radius: var(--radius);
      transition: color var(--transition-fast), background var(--transition-fast);
    }

    .notification-close:hover {
      color: var(--text);
      background: var(--surface-hover);
    }

    .notification-close:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }

    /* Type-specific styles */
    .notification-info {
      border-left: 4px solid var(--info);
    }

    .notification-success {
      border-left: 4px solid var(--success);
    }

    .notification-warning {
      border-left: 4px solid var(--warning);
    }

    .notification-error {
      border-left: 4px solid var(--danger);
    }

    /* Mobile adjustments */
    @media (max-width: 480px) {
      .notification-container {
        top: auto;
        bottom: 1rem;
        left: 1rem;
        right: 1rem;
        max-width: none;
      }
    }
  `]
})
export class NotificationContainerComponent {
  private readonly notificationService = inject(NotificationService);

  notifications = computed(() => this.notificationService.notifications$());

  @HostBinding('class.hidden')
  get isHidden() {
    return this.notifications().length === 0;
  }

  remove(id: string): void {
    this.notificationService.remove(id);
  }
}