import { expect, test, type Page } from '@playwright/test';

import { readFixture } from './fixture-data';

/**
 * Contrato del filtro por cadena.
 *
 * El backend filtra antes de paginar (`chain` viaja en la búsqueda), así que
 * este spec verifica tres cosas que la UI podría romper por separado:
 *   1. los chips se construyen con las cadenas reales de `GET /chains`;
 *   2. el chip activo se refleja en la URL y en `aria-pressed`;
 *   3. los resultados que quedan pertenecen todos a esa cadena.
 */
const fixture = readFixture();
const target = fixture.product.chains[0];

if (!target) {
  throw new Error('El fixture no tiene un producto multi-cadena; corré "make demo".');
}

async function openSearch(page: Page, params: string): Promise<void> {
  await page.goto(`/?${params}`);
  await expect(page.locator('.results .card').first()).toBeVisible({ timeout: 15_000 });
}

test.describe('filtro por cadena', () => {
  test('el selector muestra las cadenas reales y "Todas" está activo', async ({ page }) => {
    await openSearch(page, `q=${encodeURIComponent(fixture.product.name)}`);

    const picker = page.locator('.picker[aria-label="Filtrar por cadena"]');
    await expect(picker).toBeVisible();
    await expect(picker.getByRole('button')).toHaveCount(fixture.chains.length + 1);
    await expect(picker.getByRole('button', { name: /^Todas \(\d+\)$/ })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
  });

  test('click en una cadena filtra resultados y actualiza la URL', async ({ page }) => {
    await openSearch(page, `q=${encodeURIComponent(fixture.product.name)}`);

    const filtered = page.waitForResponse(
      (response) =>
        response.url().includes('/graphql') &&
        response.request().postData()?.includes('searchProducts') === true &&
        response.request().postData()?.includes(target.chain) === true
    );

    await page
      .locator('.picker[aria-label="Filtrar por cadena"] button')
      .filter({ hasText: target.chainName })
      .first()
      .click();

    await filtered;
    await expect(page).toHaveURL(new RegExp(`[?&]chain=${target.chain}(&|$)`));
    await expect(
      page.locator('.picker[aria-label="Filtrar por cadena"] button[aria-pressed="true"]')
    ).toContainText(target.chainName);

    const chains = page.locator('.results .card .card-side .card-meta');
    await expect(chains.first()).toBeVisible();
    for (const meta of await chains.allInnerTexts()) {
      expect(meta, `resultado de otra cadena: ${meta}`).toContain(target.chainName);
    }
  });

  test('la URL con chain= se abre directamente y "Todas" limpia el filtro', async ({ page }) => {
    await openSearch(
      page,
      `q=${encodeURIComponent(fixture.product.name)}&chain=${target.chain}`
    );

    const picker = page.locator('.picker[aria-label="Filtrar por cadena"]');
    await expect(picker.locator('button[aria-pressed="true"]')).toContainText(target.chainName);

    await picker.getByRole('button', { name: /^Todas \(\d+\)$/ }).click();

    await expect(page).not.toHaveURL(/[?&]chain=/);
    await expect(picker.locator('button[aria-pressed="true"]')).toContainText('Todas');
    await expect(page.locator('.results .card').first()).toBeVisible();
  });
});
