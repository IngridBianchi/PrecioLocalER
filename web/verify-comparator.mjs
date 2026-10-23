import { chromium } from '@playwright/test';

const base = 'http://localhost:4300';
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'es-AR' })).newPage();
page.on('console', (m) => m.type() === 'error' && console.log('CONSOLE ERROR:', m.text()));
page.on('response', (r) => r.status() >= 400 && console.log('HTTP', r.status(), r.url()));

await page.goto(base + '/comparador', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1500);

const input = page.getByLabel('Buscar producto');

async function addProduct(term, index) {
  await input.fill(term);
  await page.waitForSelector('#search-results li', { timeout: 15000 });
  const count = await page.locator('#search-results li').count();
  console.log(`búsqueda "${term}" → ${count} resultados`);
  await page.locator('#search-results li').nth(index).click();
  await page.waitForTimeout(1200);
}

await addProduct('DEMO', 0);
await addProduct('DEMO', 1);

console.log('chips seleccionados:', await page.locator('.selected-chips .chip').allInnerTexts());

const compareBtn = page.getByRole('button', { name: 'Comparar' });
console.log('botón comparar disabled:', await compareBtn.isDisabled());
await compareBtn.click();
await page.waitForTimeout(2500);

const panel = page.locator('.comparison-panel');
const visible = (await panel.count()) > 0 && (await panel.isVisible().catch(() => false));
console.log('panel comparación visible:', visible);
if (visible) {
  console.log('headers:', await panel.locator('thead th').allInnerTexts());
  console.log('filas:', await panel.locator('tbody tr').count());
  console.log('precios:', await panel.locator('.price').allInnerTexts());
  console.log('sin precio:', await panel.locator('.no-price').count());
}
await page.screenshot({ path: 'C:/Users/vparr/AppData/Local/Temp/opencode/shots/comparator-flow.png', fullPage: true });
await browser.close();
