import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { Chain } from './models';

/**
 * Selector de cadena reutilizado por búsqueda y detalle.
 *
 * Las cadenas vienen de `GET /chains`, no de una constante del bundle: una
 * cadena nueva en la DB aparece en la UI sin tocar el frontend.
 */
@Component({
  selector: 'app-chain-picker',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="picker" role="group" [attr.aria-label]="label()">
      <button
        type="button"
        class="chip"
        [class.active]="value() === null"
        [attr.aria-pressed]="value() === null"
        (click)="pick(null)"
      >
        Todas ({{ totalCount() }})
      </button>
      @for (chain of chains(); track chain.slug) {
        <button
          type="button"
          class="chip"
          [class.active]="value() === chain.slug"
          [class.disabled]="(counts().get(chain.slug) ?? 0) === 0"
          [attr.aria-pressed]="value() === chain.slug"
          [attr.aria-disabled]="(counts().get(chain.slug) ?? 0) === 0"
          [disabled]="(counts().get(chain.slug) ?? 0) === 0"
          (click)="pick(chain.slug)"
        >
          {{ chain.name }} ({{ counts().get(chain.slug) ?? 0 }})
        </button>
      }
    </div>
  `,
  styles: [
    `
      .picker {
        display: flex;
        flex-wrap: wrap;
        gap: 0.3rem;
      }
      .chip {
        font: inherit;
        display: inline-flex;
        align-items: center;
        min-height: 44px;
        font-size: 0.85rem;
        line-height: 1.2;
        padding: 0.35rem 0.7rem;
        border: 1px solid var(--border);
        border-radius: 999px;
        background: var(--surface);
        color: var(--text);
        cursor: pointer;
        box-shadow: var(--shadow-sm);
transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;
      }
      .chip:hover {
        border-color: var(--accent);
        color: var(--accent);
      }
      .chip:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
      }
      .chip.active {
        background: var(--accent);
        border-color: var(--accent);
        color: #fff;
      }
      .chip.disabled {
        opacity: 0.45;
        cursor: not-allowed;
        box-shadow: none;
      }
    `
  ]
})
export class ChainPickerComponent {
  readonly chains = input.required<Chain[]>();
  readonly value = input<string | null>(null);
  readonly label = input('Filtrar por cadena');
  readonly counts = input<Map<string, number>>(new Map());
  readonly totalCount = input<number>(0);
  readonly valueChange = output<string | null>();

  pick(slug: string | null): void {
    this.valueChange.emit(slug);
  }
}