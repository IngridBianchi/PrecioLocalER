import { defineConfig, devices } from '@playwright/test';

/**
 * E2E real de navegador (Playwright) contra la stack completa.
 *
 * Por qué `E2E_BASE_URL` y no un `webServer` propio: la app se sirve por nginx
 * (`web/docker/nginx.conf`), que es quien decide qué rutas del API van al
 * scraper y cuáles a .NET. Servir el bundle con otro proxy testearía una
 * topología que nadie despliega y volvería invisible la clase de bug que ya
 * mordió una vez (quitar `chains` del `location` y la UI recibía index.html en
 * vez de JSON). El suite pega contra lo que está levantado:
 *
 *     docker compose up -d            # postgres, scraper, ml-dotnet, web (nginx)
 *     cd web && npm run e2e
 *
 * Variables:
 * - `E2E_BASE_URL`: origen de la app (default http://127.0.0.1:4200, el puerto
 *   publicado por el servicio `web` del compose).
 * - `E2E_QUERY`: término de búsqueda (default `aceite`).
 */
const baseURL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:4200';

export default defineConfig({
  testDir: './e2e',
  outputDir: './e2e/.artifacts',
  timeout: 45_000,
  expect: { timeout: 15_000 },
  // La base es compartida con la de desarrollo: los specs son de lectura
  // (GET) y el descubrimiento de datos es idempotente, pero dos workers
  // escribiendo reportes al mismo tiempo no aportan nada.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list']],
  globalSetup: './e2e/global-setup.ts',
  use: {
    baseURL,
    locale: 'es-AR',
    timezoneId: 'America/Argentina/Buenos_Aires',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure'
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }]
});
