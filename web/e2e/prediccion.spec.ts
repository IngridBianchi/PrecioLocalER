import { expect, test } from '@playwright/test';

import { readFixture } from './fixture-data';

/**
 * Flujo de predicción (ADR-0006): la banda a 7 días tiene que aparecer aunque
 * uno de los dos motores esté caído, y la UI tiene que decir por qué se
 * degradó. Cuando ambos responden, la banda es el contraste de los dos.
 */
const fixture = readFixture();

test.describe('predicción de precio', () => {
  test('el detalle muestra 7 días de banda con rango ordenado', async ({ page }) => {
    await page.goto(`/producto/${fixture.product.id}`);

    const card = page.locator('section[aria-label="Predicción de precio"]');
    await expect(card).toBeVisible();
    await expect(card.getByRole('heading', { name: 'Predicción a 7 días' })).toBeVisible();

    const rows = card.locator('table.band tbody tr');
    await expect(rows).toHaveCount(7);

    for (const row of await rows.all()) {
      const range = (await row.locator('td').nth(1).innerText()).split('–');
      expect(range).toHaveLength(2);
      const low = Number(range[0].replace(/[^\d,]/g, '').replace(',', '.'));
      const high = Number(range[1].replace(/[^\d,]/g, '').replace(',', '.'));
      expect(Number.isFinite(low)).toBe(true);
      expect(Number.isFinite(high)).toBe(true);
      expect(low).toBeGreaterThan(0);
      expect(high).toBeGreaterThanOrEqual(low);
    }
  });

  test('el resumen dice si los dos modelos coinciden o si la banda es débil', async ({
    page
  }) => {
    await page.goto(`/producto/${fixture.product.id}`);

    const card = page.locator('section[aria-label="Predicción de precio"]');
    const weak = card.getByText('Un solo modelo respondió');
    const agree = card.getByText(/Holt-Winters y SsaForecaster coinciden en el/);

    const bothEngines = fixture.python.points > 0 && fixture.dotnet.points > 0;
    if (bothEngines) {
      await expect(agree).toBeVisible();
      await expect(weak).toHaveCount(0);
    } else {
      // Con un motor caído la banda se conserva y el motivo queda explícito:
      // esto es el requisito del ADR-0006, no un fallback silencioso.
      await expect(weak).toBeVisible();
      await expect(card.locator('ul.degraded li').first()).toBeVisible();
    }
  });

  test('cada degradación reportada por el API aparece en la UI', async ({ page }) => {
    await page.goto(`/producto/${fixture.product.id}`);

    const items = page.locator('section[aria-label="Predicción de precio"] ul.degraded li');
    const expected = [fixture.python, fixture.dotnet]
      .filter((model) => model.reachable && model.degraded)
      .map((model) => `${model.model}: ${model.degraded}`);

    for (const reason of expected) {
      // Un modelo que degrada sigue proyectando, pero la UI tiene que decir
      // cuál corrió realmente y por qué.
      await expect(items.filter({ hasText: reason })).toHaveCount(1);
    }
    if (expected.length === 0) {
      await expect(items).toHaveCount(0);
    }
  });

  test('el gráfico de evolución dibuja algo y cambia con el período', async ({ page }) => {
    await page.goto(`/producto/${fixture.product.id}`);

    const canvas = page.locator('canvas[aria-label="Gráfico de evolución de precio y banda de predicción"]');
    await expect(canvas).toBeVisible();

    const drawn = async (): Promise<number> =>
      canvas.evaluate((el) => {
        const node = el as HTMLCanvasElement;
        const ctx = node.getContext('2d');
        if (!ctx || node.width === 0 || node.height === 0) {
          return 0;
        }
        const { data } = ctx.getImageData(0, 0, node.width, node.height);
        let painted = 0;
        for (let i = 3; i < data.length; i += 4) {
          if (data[i] !== 0) {
            painted += 1;
          }
        }
        return painted;
      });

    await expect.poll(drawn, { message: 'el canvas no tiene píxeles pintados' }).toBeGreaterThan(0);

    const active = page.locator('.spans .span.active');
    await expect(active).toHaveText('30 días');
    await page.locator('.spans .span', { hasText: '7 días' }).click();
    await expect(active).toHaveText('7 días');
    await expect.poll(drawn).toBeGreaterThan(0);
  });

  test('el detalle de un producto inexistente no rompe la página', async ({ page }) => {
    await page.goto('/producto/99999999');

    // El 404 del API tiene que producir un estado de error usable, no una
    // pantalla en blanco ni un error de JavaScript sin capturar.
    await expect(page.getByRole('alert')).toHaveText('No se pudo cargar el producto solicitado.');
    await expect(page.getByRole('link', { name: '← Volver a la búsqueda' })).toBeVisible();
    await expect(page.locator('section[aria-label="Precio vigente"]')).toHaveCount(0);
  });

  test('no hay errores de JavaScript ni pedidos fallidos en el flujo feliz', async ({ page }) => {
    const consoleErrors: string[] = [];
    const failed: string[] = [];
    page.on('console', (message) => {
      if (message.type() === 'error') {
        consoleErrors.push(message.text());
      }
    });
    page.on('requestfailed', (request) => {
      failed.push(`${request.method()} ${request.url()} → ${request.failure()?.errorText}`);
    });

    await page.goto(`/producto/${fixture.product.id}`);
    await expect(page.locator('table.band tbody tr')).toHaveCount(7);

    expect(failed, `requests fallidos: ${failed.join(' | ')}`).toHaveLength(0);
    expect(consoleErrors, `errores de consola: ${consoleErrors.join(' | ')}`).toHaveLength(0);
  });
});
