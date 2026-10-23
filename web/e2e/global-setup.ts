import type { APIRequestContext, FullConfig } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

import { FIXTURE_FILE, FixtureData, ModelInfo } from './fixture-data';

/**
 * Descubre, por la misma API que consume el browser, un producto que ejercite
 * los tres flujos del PLAN §5.
 *
 * Se ejecuta una vez antes de los specs y falla ruidosamente si la stack no
 * está o si no hay datos: un E2E que se pone verde porque no encontró nada es
 * peor que uno que no corre, porque miente sobre el estado del producto.
 */
interface SearchHit {
  id: number;
  name: string;
  chain_name: string;
  price: number;
}

interface SearchPage {
  products: SearchHit[];
  total: number;
}

interface Detail {
  id: number;
  name: string;
  prices: { chain: string; price: string | number }[];
}

interface PythonPayload {
  model?: string;
  degraded_reason?: string | null;
  points?: unknown[];
}

interface DotnetPayload {
  model?: string;
  degradedReason?: string | null;
  points?: unknown[];
}

async function getJson<T>(
  request: APIRequestContext,
  path: string
): Promise<{ status: number; body: T | null }> {
  const response = await request.get(path);
  return { status: response.status(), body: response.ok() ? await response.json() : null };
}

async function readModel(
  request: APIRequestContext,
  path: string,
  degradedKey: 'degraded_reason' | 'degradedReason'
): Promise<ModelInfo> {
  const { status, body } = await getJson<PythonPayload & DotnetPayload>(request, path);
  if (status !== 200 || !body) {
    return { reachable: false, model: null, points: 0, degraded: null };
  }
  return {
    reachable: true,
    model: body.model ?? null,
    points: body.points?.length ?? 0,
    degraded: (body[degradedKey] as string | null) ?? null
  };
}

export default async function globalSetup(config: FullConfig): Promise<void> {
  // Si el fixture ya existe y tiene datos válidos, saltar la generación
  try {
    const existing = JSON.parse(await import('node:fs').then(fs => fs.promises.readFile(FIXTURE_FILE, 'utf8')));
    if (existing?.product?.id && existing.chains?.length) {
      console.log('[global-setup] Fixture ya existe y es válida, saltando generación');
      return;
    }
  } catch {
    // No existe o es inválido, continuar con la generación
  }
  const baseURL = (config.projects[0]?.use?.baseURL as string) ?? 'http://127.0.0.1:4200';
  const query = (process.env.E2E_QUERY ?? 'aceite').trim();
  const { request } = await import('@playwright/test');
  const api = await request.newContext({ baseURL });

  try {
    const chains = await getJson<{ slug: string; name: string }[]>(api, '/chains');
    if (chains.status !== 200 || !chains.body?.length) {
      throw new Error(
        chains.status === 429
          ? `GET /chains → 429. El rate limiter del scraper (RATE_LIMIT_REQUESTS_PER_MINUTE, ` +
              `30 por default) bloquea al suite: todo el tráfico del browser llega por nginx ` +
              `con la misma IP. Subilo antes de correr el E2E: "echo ` +
              `'RATE_LIMIT_REQUESTS_PER_MINUTE=1000' >> .env && docker compose up -d scraper"`
          : `GET /chains → ${chains.status}. ¿Está la stack arriba (docker compose up -d) y el ` +
              'proxy de nginx ruteando /chains al scraper (web/docker/nginx.conf)?'
      );
    }

    const hits = await getJson<SearchPage>(api, `/products?q=${encodeURIComponent(query)}`);
    if (hits.status !== 200) {
      throw new Error(`GET /products?q=${query} → ${hits.status}`);
    }
    if (!hits.body?.products.length) {
      throw new Error(
        `GET /products?q=${query} → 0 resultados. Cargá datos con un scrape real ` +
          'o con "make demo" (scripts/seed_demo_history.py).'
      );
    }

    // El producto de referencia tiene que estar en 2+ cadenas porque así es como
    // se ejercita el comparativo. Con los catálogos reales de Fase 1 eso casi no
    // ocurre (cada cadena scrapea su propio id de producto, así que el mismo
    // nombre no se agrupa entre cadenas): los productos multi-cadena son los del
    // seed DEMO. Por eso el discovery prueba varios términos, y el que aparece
    // primero en la escalera es el que se usa.
    const ladder = [process.env.E2E_FIXTURE_QUERY ?? 'DEMO', query];
    const tried: string[] = [];
    let chosen: { detail: Detail; python: ModelInfo; dotnet: ModelInfo } | undefined;
    for (const term of ladder) {
      const search = await getJson<SearchPage>(api, `/products?q=${encodeURIComponent(term)}`);
      tried.push(`${term}=${search.body?.products.length ?? 0}`);
      if (search.status !== 200 || !search.body?.products.length) {
        continue;
      }
      for (const hit of search.body.products.slice(0, 20)) {
        const detail = await getJson<Detail>(api, `/products/${hit.id}`);
        if (detail.status !== 200 || !detail.body || detail.body.prices.length < 2) {
          continue;
        }
        const python = await readModel(api, `/ml/forecast/${hit.id}?horizon=7`, 'degraded_reason');
        const dotnet = await readModel(api, `/api/forecast/${hit.id}?horizon=7`, 'degradedReason');
        if (python.points > 0) {
          chosen = { detail: detail.body, python, dotnet };
          break;
        }
      }
      if (chosen) {
        break;
      }
    }

    if (!chosen) {
      throw new Error(
        'No se encontró ningún producto con precio en 2+ cadenas y forecast con puntos ' +
          `(términos probados: ${tried.join(', ')}). Corré "make demo" para generar el ` +
          'histórico sintético multi-cadena y "make train" para persistir los modelos.'
      );
    }

    const chainNames = new Map(chains.body.map((c) => [c.slug, c.name]));
    const fixture: FixtureData = {
      baseURL,
      query,
      chains: chains.body,
      product: {
        id: chosen.detail.id,
        name: chosen.detail.name,
        chainCount: chosen.detail.prices.length,
        chains: chosen.detail.prices.map((p) => ({
          chain: p.chain,
          chainName: chainNames.get(p.chain) ?? p.chain,
          price: Number(p.price)
        }))
      },
      python: chosen.python,
      dotnet: chosen.dotnet
    };

    mkdirSync(dirname(FIXTURE_FILE), { recursive: true });
    writeFileSync(FIXTURE_FILE, `${JSON.stringify(fixture, null, 2)}\n`, 'utf8');

    const engines = [
      `python=${chosen.python.model ?? '?'}(${chosen.python.points}p)`,
      `dotnet=${
        chosen.dotnet.reachable ? `${chosen.dotnet.model ?? '?'}(${chosen.dotnet.points}p)` : 'caído'
      }`
    ].join(' ');
    console.log(
      `[e2e] fixture: #${fixture.product.id} "${fixture.product.name}" · ` +
        `${fixture.product.chainCount} cadenas · ${engines}`
    );
  } finally {
    await api.dispose();
  }
}
