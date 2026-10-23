import { Component, OnInit, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { NotificationContainerComponent } from './core/notification.component';

@Component({
  selector: 'app-root',
  imports: [RouterLink, RouterLinkActive, RouterOutlet, NotificationContainerComponent],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class App implements OnInit {
  readonly theme = signal<'light' | 'dark'>('light');

  ngOnInit(): void {
    const stored = typeof localStorage !== 'undefined' ? localStorage.getItem('pl-theme') : null;
    const prefersDark =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-color-scheme: dark)').matches;
    const initial: 'light' | 'dark' =
      stored === 'dark' || stored === 'light' ? stored : prefersDark ? 'dark' : 'light';
    this.theme.set(initial);
    this.applyTheme(initial);
  }

  toggleTheme(): void {
    const next = this.theme() === 'dark' ? 'light' : 'dark';
    this.theme.set(next);
    this.applyTheme(next);
    try {
      localStorage.setItem('pl-theme', next);
    } catch {
      /* ignore */
    }
  }

  private applyTheme(value: 'light' | 'dark'): void {
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('data-theme', value);
    }
  }
}