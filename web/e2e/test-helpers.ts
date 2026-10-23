import { expect, Page } from '@playwright/test';

/**
 * Helper para realizar búsquedas usando la API de test expuesta por el componente SearchComponent.
 * Esto evita los problemas con `page.fill()` que no dispara valueChanges del FormControl.
 */
export async function performSearch(page: Page, query: string): Promise<void> {
  const input = page.locator('#q');
  await expect(input).toBeVisible();
  
  // Interceptar la respuesta de búsqueda GraphQL ANTES de disparar la búsqueda
  const searchResponse = page.waitForResponse(response => 
    response.url().includes('/graphql') && 
    response.request().postData()?.includes('searchProducts') &&
    response.request().postData()?.includes(query)
  );
  
  // Usar la API de test para setear la query
  await page.waitForTimeout(500);
  await page.evaluate((q: string) => {
    const testApi = (window as any).__PRECIOLOCAL_TEST__;
    if (testApi) {
      testApi.setQuery(q);
    }
  }, query);
  
  // Verificar que el valor se setió
  await page.waitForTimeout(100);
  
  // Esperar a que la respuesta de búsqueda llegue
  const response = await searchResponse;
  expect(response.ok()).toBeTruthy();
  const data = await response.json();
  expect(data.data?.searchProducts?.products?.length).toBeGreaterThan(0);
  
  // Esperar un poco más para que la UI se actualice
  await page.waitForTimeout(500);
}