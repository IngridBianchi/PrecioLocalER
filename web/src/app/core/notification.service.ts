import { Injectable, signal, computed, inject } from '@angular/core';
import { DOCUMENT } from '@angular/common';

export type NotificationType = 'info' | 'success' | 'warning' | 'error';

export interface Notification {
  id: string;
  type: NotificationType;
  title: string;
  message: string;
  duration?: number; // ms, default 8000
}

@Injectable({ providedIn: 'root' })
export class NotificationService {
  private readonly document = inject(DOCUMENT);

  private readonly notifications = signal<Notification[]>([]);

  readonly notifications$ = computed(() => this.notifications());

  show(notification: Omit<Notification, 'id'>): string {
    const id = crypto.randomUUID();
    const notificationWithId: Notification = {
      id,
      duration: 8000,
      ...notification,
    };

    this.notifications.update(current => [...current, notificationWithId]);

    // Auto-remove after duration
    setTimeout(() => this.remove(id), notificationWithId.duration);

    return id;
  }

  info(title: string, message: string, duration?: number): string {
    return this.show({ type: 'info', title, message, duration });
  }

  success(title: string, message: string, duration?: number): string {
    return this.show({ type: 'success', title, message, duration });
  }

  warning(title: string, message: string, duration?: number): string {
    return this.show({ type: 'warning', title, message, duration });
  }

  error(title: string, message: string, duration?: number): string {
    return this.show({ type: 'error', title, message, duration });
  }

  remove(id: string): void {
    this.notifications.update(current => current.filter(n => n.id !== id));
  }

  clear(): void {
    this.notifications.set([]);
  }
}