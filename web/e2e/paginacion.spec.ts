import { expect, test, type Page } from '@playwright/test';

import { readFixture } from './fixture-data';

/**
 * Contrato de paginación real contra el backend.
 *
 * La página no pagina en memoria: `limit` y `offset` viajan al scraper y la
 * URL es la fuente de verdad. Por eso estos tests navegan con query params
 * (como lo haría un usuario al compartir el link) y verifican el total que
 * devuelve el backend, no una lista recortada por el frontend.
 */
const fixture = readFixture();
const PAGE_SIZE = 10;

async function openSearch(page: Page, params: string): Promise<void> {
  await page.goto(`/?${params}`);
  await expect(page.locator('.results .card').first()).toBeVisible({ timeout: 15_000 });
}

async function cardLinks(page: Page): Promise<string[]> {
  return page
    .locator('.results .card')
    .evaluateAll((cards) => cards.map((card) => card.getAttribute('href') ?? ''));
}

test.describe('paginación real', () => {
  let total = 0;

  test.beforeAll(async ({ request }) => {
    const response = await request.get(
      `/products?q=${encodeURIComponent(fixture.query)}&limit=1&offset=0`
    );
    expect(response.ok()).toBeTruthy();
    const body = (await response.json()) as { total: number };
    total = body.total;
    // Si el seed cambió y ya no hay más de una página, el test de página 2
    // mentiría. Fallar acá es más honesto que saltarlo en silencio.
    expect(total, `"${fixture.query}" necesita más de ${PAGE_SIZE} resultados`).toBeGreaterThan(
      PAGE_SIZE
    );
  });

  test('la primera página muestra el pageSize y el total del backend', async ({ page }) => {
    await openSearch(page, `q=${encodeURIComponent(fixture.query)}`);

    await expect(page.locator('.results .card')).toHaveCount(PAGE_SIZE);
    await expect(page.locator('.results-count')).toContainText(`1–${PAGE_SIZE} de ${total}`);
    await expect(page.locator('.pager')).toContainText('Página 1 de');
    await expect(page.getByRole('button', { name: 'Anterior' })).toBeDisabled();
  });

  test('Siguiente lleva a offset=10 y no repite los resultados de la página 1', async ({
    page
  }) => {
    await openSearch(page, `q=${encodeURIComponent(fixture.query)}&limit=10&offset=0`);
    const firstPage = await cardLinks(page);
    expect(firstPage).toHaveLength(PAGE_SIZE);

    await page.getByRole('button', { name: 'Siguiente' }).click();
    await expect(page).toHaveURL(/[?&]offset=10\b/);
    await expect(page.locator('.pager')).toContainText('Página 2 de');

    const expectedOnPage2 = Math.min(PAGE_SIZE, total - PAGE_SIZE);
    await expect(page.locator('.results .card')).toHaveCount(expectedOnPage2);
    await expect(page.locator('.results-count')).toContainText(
      `${PAGE_SIZE + 1}–${PAGE_SIZE + expectedOnPage2} de ${total}`
    );

    // El contador de la URL cambia en el mismo tick que la navegación, antes
    // de que llegue la respuesta GraphQL. Esperar a que la primera tarjeta
    // cambie evita comparar la página 2 contra la página 1 todavía pintada.
    await expect
      .poll(async () => (await cardLinks(page))[0], {
        timeout: 10_000,
        message: 'la página 2 siguió mostrando el primer resultado de la página 1'
      })
      .not.toBe(firstPage[0]);

    const secondPage = await cardLinks(page);
    for (const link of secondPage) {
      expect(firstPage, `el link ${link} se repitió entre páginas`).not.toContain(link);
    }
  });

  test('limit=5 se respeta y el paginador lo refleja', async ({ page }) => {
    await openSearch(page, `q=${encodeURIComponent(fixture.query)}&limit=5&offset=0`);

    await expect(page.locator('.results .card')).toHaveCount(5);
    await expect(page.locator('.results-count')).toContainText('1–5 de');
    await expect(page.locator('.pager')).toContainText(
      `Página 1 de ${Math.ceil(total / 5)}`
    );
  });

  test('un offset negativo se normaliza a la primera página', async ({ page }) => {
    await openSearch(page, `q=${encodeURIComponent(fixture.query)}&offset=-5&limit=10`);
    const invalidOffset = await cardLinks(page);

    await openSearch(page, `q=${encodeURIComponent(fixture.query)}&offset=0&limit=10`);
    const firstPage = await cardLinks(page);

    expect(invalidOffset).toEqual(firstPage);
  });

  test('un limit fuera de rango se normaliza sin romper la búsqueda', async ({ page }) => {
    await openSearch(page, `q=${encodeURIComponent(fixture.query)}&limit=999&offset=0`);

    const count = await page.locator('.results .card').count();
    expect(count).toBeGreaterThan(0);
    expect(count).toBeLessThanOrEqual(50);
  });

  test('la URL compartida de la página 2 se abre directamente', async ({ page }) => {
    await openSearch(page, `q=${encodeURIComponent(fixture.query)}&limit=10&offset=10`);

    await expect(page.locator('.pager')).toContainText('Página 2 de');
    await expect(page.locator('.results-count')).toContainText(`${PAGE_SIZE + 1}–`);
  });
});
