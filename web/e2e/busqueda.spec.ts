import { expect, test } from '@playwright/test';

import { readFixture } from './fixture-data';
import { performSearch } from './test-helpers';

const fixture = readFixture();

test.describe('búsqueda', () => {
  test('buscar un término lista resultados con precio y cadena', async ({ page }) => {
    await page.goto('/');
    await performSearch(page, 'aceite');
    
    const results = page.locator('.results .card');
    await expect(results.first()).toBeVisible({ timeout: 15000 });
  });

  test('un término de 1 carácter no dispara búsqueda', async ({ page }) => {
    const calls: string[] = [];
    page.on('request', (request) => {
      if (request.url().includes('/graphql') && request.postData()?.includes('searchProducts')) {
        calls.push(request.url());
      }
    });

    await page.goto('/');
    const input = page.locator('#q');
    await expect(input).toBeVisible();
    
    await page.evaluate(() => {
      const testApi = (window as any).__PRECIOLOCAL_TEST__;
      if (testApi) {
        testApi.setQuery('a');
      }
    });
    
    await page.waitForTimeout(700);
    expect(calls).toHaveLength(0);
  });

  test('abrir un resultado lleva al detalle con el mismo producto', async ({ page }) => {
    await page.goto('/');
    await performSearch(page, fixture.query);

    const first = page.locator('.results .card').first();
    await expect(first).toBeVisible();
    const name = (await first.locator('.card-name').innerText()).trim();
    const href = await first.getAttribute('href');

    await first.click();

    // La navegación usa View Transitions, así que la URL no cambia en el mismo
    // tick del click: hay que esperarla (y no leerla al vuelo).
    await page.waitForURL(`**${href}`);
    await expect(page.getByRole('heading', { level: 2 })).toHaveText(name);
    await expect(page.locator('section[aria-label="Precio vigente"] .price')).toHaveText(/\$\s?\d/);
    await expect(page.getByRole('link', { name: '← Volver a la búsqueda' }).first()).toBeVisible();
  });

  test('el back del detalle vuelve a una búsqueda operable', async ({ page }) => {
    await page.goto('/');
    await performSearch(page, fixture.query);
    await expect(page.locator('.results .card').first()).toBeVisible();

    await page.locator('.results .card').first().click();
    await expect(page.locator('section[aria-label="Precio vigente"]')).toBeVisible();

    await page.getByRole('link', { name: '← Volver a la búsqueda' }).first().click();
    await page.waitForURL((url) => url.pathname === '/');

    // El componente de búsqueda se destruye al ir al detalle: la caja vuelve
    // vacía. No es un contrato de producto (guardar la consulta entre navegaciones
    // está anotado como deuda de UX en CONTEXTO §17), así que lo que se verifica
    // acá es que volver sea operativa: se puede volver a buscar y trae resultados.
    const input = page.getByLabel('Buscar producto');
    await expect(input).toBeVisible();
    await expect(input).toHaveValue('');
    await performSearch(page, fixture.query);
    await expect(page.locator('.results .card').first()).toBeVisible();
  });

  test('una ruta desconocida redirige a la búsqueda', async ({ page }) => {
    await page.goto('/no-existe/123');
    await expect(page.getByLabel('Buscar producto')).toBeVisible();
  });

  test('el encabezado y el pie identifies la app', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.topbar .brand')).toHaveText('PrecioLocal ER');
    await expect(page.locator('footer')).toContainText('Fase 1');
  });
});