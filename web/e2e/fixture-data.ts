import { readFileSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Contrato de los datos que el E2E necesita y de dónde los saca.
 *
 * El suite no puede hardcodear IDs de producto: la base cambia con cada scrape
 * (`seed_demo_history`, scrapes reales) y un `id: 1` fijo rompería la suite sin
 * que nada del código haya estado mal. Lo que sí es estable es el *contrato* de
 * lo que tiene que haber en la base para que la UI pueda mostrar algo: al menos
 * un producto con precio en dos cadenas y con serie suficiente para proyectar.
 * `global-setup.ts` lo descubre por la misma API que usa el browser y lo deja
 * en `e2e/.data/fixture.json`.
 */
export interface ChainPrice {
  chain: string;
  chainName: string;
  price: number;
}

export interface ModelInfo {
  reachable: boolean;
  model: string | null;
  points: number;
  degraded: string | null;
}

export interface FixtureData {
  /** Origen contra el que se corrió (baseURL de la config). */
  baseURL: string;
  /** Término de búsqueda usado; viene de E2E_QUERY. */
  query: string;
  chains: { slug: string; name: string }[];
  /** Producto de referencia: multi-cadena y con serie proyectable. */
  product: {
    id: number;
    name: string;
    chainCount: number;
    chains: ChainPrice[];
  };
  python: ModelInfo;
  dotnet: ModelInfo;
}

export const FIXTURE_FILE = join(__dirname, '.data', 'fixture.json');

export function readFixture(): FixtureData {
  return JSON.parse(readFileSync(FIXTURE_FILE, 'utf8')) as FixtureData;
}
