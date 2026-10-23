import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Guard de la configuración de proxy.
 *
 * No necesita browser: es un test de contrato sobre `web/docker/nginx.conf` y
 * `web/proxy.conf.json`. Vive en la suite E2E porque el fallo que previene es
 * exactamente el que el E2E detecta (la UI mostrando `index.html` donde
 * esperaba JSON, el 404 silencioso del filtro de cadenas), y porque las rutas
 * que el front consume salen de los servicios Angular: si un endpoint nuevo no
 * está en el proxy, ningún unit test se entera hasta que alguien lo abre a mano
 * contra la stack.
 */
const WEB_ROOT = join(__dirname, '..');
const REPO_ROOT = join(WEB_ROOT, '..');
const NGINX = readFileSync(join(WEB_ROOT, 'docker', 'nginx.conf'), 'utf8');
const COMPOSE = readFileSync(join(REPO_ROOT, 'docker-compose.yml'), 'utf8');
const SCRAPER_DOCKERFILE = readFileSync(join(REPO_ROOT, 'services', 'scraper', 'Dockerfile'), 'utf8');
// Los comentarios del Dockerfile explican lo que sequitó (`"*"`), así que las
// aserciones de config van contra el archivo sin comentarios: si no, el test
// pasa por accidentey deja pasar la regresión.
const DOCKERFILE_RULES = SCRAPER_DOCKERFILE.replace(/^\s*#.*$/gm, '');
// Los comentarios de nginx.conf mencionan `location /` al explicar por qué
// `chains` está en el primer bloque: sin sacarlos, el parser matchea la
// mención del comentario en vez del bloque real.
const NGINX_RULES = NGINX.replace(/^\s*#.*$/gm, '');
const DEV_PROXY = JSON.parse(readFileSync(join(WEB_ROOT, 'proxy.conf.json'), 'utf8')) as Record<
  string,
  { target: string }
>;

/** Fuentes Angular que arman las URLs del API. */
const API_SOURCES = ['shared/forecast-api.service.ts', 'core/apollo.config.ts'] as const;

interface NginxLocation {
  kind: string;
  pattern: string;
  upstream: string;
  denies: boolean;
}

/** Ubica cada `location` de nginx con el upstream al que reenvía. */
function parseLocations(): NginxLocation[] {
  const locations: NginxLocation[] = [];
  const block = /^\s{4}location\s+(~\*|~|=|)\s*([^{]*?)\s*\{([\s\S]*?)^\s{4}\}/gm;
  for (const [, kind, pattern, body] of NGINX_RULES.matchAll(block)) {
    const upstream = body.match(/proxy_pass\s+http:\/\/([\w-]+):(\d+);/);
    locations.push({
      kind,
      pattern: pattern.trim(),
      upstream: upstream ? `${upstream[1]}:${upstream[2]}` : 'static',
      denies: /deny\s+all;/.test(body)
    });
  }
  return locations;
}

const LOCATIONS = parseLocations();

/**
 * A qué upstream manda nginx una request, o `static` si cae en la SPA.
 *
 * Respeta el orden de resolución de nginx: `=` exacto gana, después las
 * regex por orden de aparición, y recién al final el prefijo más largo. Sin
 * ese orden, `location /` (que aparece antes en el archivo) se tragaría a las
 * regex y el test daría por bueno un `/chains` sin proxear.
 */
function upstreamFor(path: string): string {
  const resolve = (location: NginxLocation): string =>
    location.denies ? 'deny' : location.upstream;

  const exact = LOCATIONS.find((l) => l.kind === '=' && l.pattern === path);
  if (exact) {
    return resolve(exact);
  }

  const regex = LOCATIONS.find(
    (l) =>
      (l.kind === '~' || l.kind === '~*') &&
      new RegExp(l.pattern, l.kind === '~*' ? 'i' : '').test(path)
  );
  if (regex) {
    return resolve(regex);
  }

  const prefix = LOCATIONS.filter(
    (l) => !l.kind && path.startsWith(l.pattern)
  ).sort((a, b) => b.pattern.length - a.pattern.length)[0];
  return prefix ? resolve(prefix) : 'none';
}

/** Prefijos que el front pide, leídos de los templates de los servicios. */
function calledPrefixes(): string[] {
  const prefixes = new Set<string>();
  for (const source of API_SOURCES) {
    const code = readFileSync(join(WEB_ROOT, 'src', 'app', ...source.split('/')), 'utf8');
    for (const match of code.matchAll(/\$\{(?:this\.base|environment\.[A-Za-z]+)\}(\/[\w-]+)/g)) {
      prefixes.add(match[1]);
    }
  }
  return [...prefixes].sort();
}

test.describe('contrato de proxy', () => {
  test('se parsearon las locations esperadas de nginx', () => {
    expect(LOCATIONS.length).toBeGreaterThanOrEqual(5);
    expect(upstreamFor('/chains')).toBe('scraper:8000');
    expect(upstreamFor('/api/forecast/1')).toBe('ml-dotnet:8001');
  });

  test('todo endpoint que llama el front está proxado a su servicio', () => {
    const prefixes = calledPrefixes();
    expect(prefixes.length).toBeGreaterThanOrEqual(4);

    const upstreamByPrefix: Record<string, string> = {
      '/api': 'ml-dotnet:8001',
      '/graphql': 'gateway:4000',
      '/ws': 'gateway:4000',
      '/ml': 'scraper:8000'
    };

    for (const prefix of prefixes) {
      const upstream = upstreamFor(prefix);
      const expected = upstreamByPrefix[prefix];
      expect(expected, `no hay upstream declarado para ${prefix}`).toBeDefined();
      expect(
        upstream,
        `nginx.conf manda "${prefix}" a ${upstream}: la UI pediría index.html en lugar ` +
          `de JSON (o el 404 silencioso del filtro de cadenas).`
      ).toBe(expected);
    }
  });

  test('las rutas de la SPA caen en index.html y los dotfiles se niegan', () => {
    expect(upstreamFor('/')).toBe('static');
    expect(upstreamFor('/producto/42')).toBe('static');
    expect(upstreamFor('/.git/config')).toBe('deny');
  });

  test('el proxy de desarrollo cubre las mismas rutas que nginx', () => {
    const dev = Object.keys(DEV_PROXY);
    for (const path of ['/chains', '/products', '/ml', '/api', '/graphql', '/ws']) {
      expect(dev, `proxy.conf.json no proxea ${path}`).toContain(path);
      expect(upstreamFor(path), `nginx.conf no proxea ${path}`).not.toBe('static');
    }
    expect(DEV_PROXY['/ml'].target).toBe('http://localhost:8000');
    expect(DEV_PROXY['/api'].target).toBe('http://localhost:8001');
    expect(DEV_PROXY['/graphql'].target).toBe('http://localhost:4000');
    expect(DEV_PROXY['/ws'].target).toBe('http://localhost:4000');
  });

  test('la CSP del front permite lo que la app usa y nada más', () => {
    const csp = NGINX_RULES.match(/Content-Security-Policy "([^"]+)"/)?.[1] ?? '';
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("connect-src 'self'");
    expect(csp).not.toContain('unsafe-eval');
    expect(csp).not.toContain("script-src 'self' 'unsafe-inline'");
  });
});

/**
 * Tanda 1 de MEJORAS.md (P0.2 y P0.3): el rate limit tiene que partir por la
 * IP real del cliente, y `POST /scrape/run/{chain}` no se expone por el dominio
 * público. Las dos son de configuración, y por definición no se
 * rompen solas: nada falla si alguien reintroduce `$proxy_add_x_forwarded_for`
 * o saca el `deny`. Por eso se testean como contrato, y el `deny` además se
 * comprueba contra el nginx vivo (un test de config no probaría que nginx
 * aplique la regla).
 */
test.describe('frontera de confianza del proxy', () => {
  test('nginx sobreescribe X-Forwarded-For con la IP real del peer', () => {
    expect(
      NGINX_RULES,
      'nginx.conf vuelve a usar $proxy_add_x_forwarded_for: hace APPEND del XFF ' +
        'entrante, así que un cliente que mande `X-Forwarded-For: 1.2.3.4` pasa a ' +
        'ser la IP de cliente en el scraper y se salta su rate limit.'
    ).not.toContain('$proxy_add_x_forwarded_for');

    const proxying = LOCATIONS.filter((l) => l.upstream !== 'static');
    expect(proxying.length).toBeGreaterThanOrEqual(2);
    for (const location of proxying) {
      const block = NGINX_RULES.match(
        new RegExp(`location\\s+${location.kind}\\s*${escapeRe(location.pattern)}\\s*\\{[\\s\\S]*?^\\s{4}\\}`, 'm')
      )?.[0];
      expect(block, `no se encontró el bloque de ${location.pattern}`).toContain(
        'proxy_set_header X-Forwarded-For $remote_addr;'
      );
    }
  });

  test('el proxy de entrada es la única IP confiable, y nginx tiene esa IP', () => {
    // Allow-list del scraper: una IP, no un rango (el gateway está en la subred
    // y tampoco debe poder firmar XFF).
    expect(
      DOCKERFILE_RULES,
      'el scraper volvió a confiar en cualquier peer (--forwarded-allow-ips "*"), ' +
        'con lo que cualquier cliente del puerto 8000 elige su propio bucket.'
    ).not.toContain('"*"');
    expect(DOCKERFILE_RULES).toContain('--forwarded-allow-ips ${TRUSTED_PROXY_IP');
    // La forma shell del CMD obliga a `exec`: sin él, uvicorn queda como hijo
    // del shell, `docker stop` le manda SIGTERM al shell (que no reenvía) y el
    // scraper se corta por SIGKILL a los 10 s, con scrapes en vuelo.
    expect(DOCKERFILE_RULES).toMatch(/CMD\s+exec\s+uvicorn/);

    // La misma variable tiene que alimentar las tres piezas: la allow-list del
    // scraper, la de .NET y la IP fija de nginx. Si divergen, uvicorn ignora el
    // XFF (bucket global otra vez) sin ningún error visible.
    const trusted = COMPOSE.match(/\$\{TRUSTED_PROXY_IP:-([\d.]+)\}/g) ?? [];
    expect(trusted.length, 'compose no usa TRUSTED_PROXY_IP en los tres lugares esperados').toBe(3);
    expect(new Set(trusted).size, 'cada consumidor declara un TRUSTED_PROXY_IP distinto').toBe(1);

    // Dos como variable de entorno (scraper y ml-dotnet) y uno como IP fija.
    const envUses = COMPOSE.match(/^\s+TRUSTED_PROXY_IP: \$\{TRUSTED_PROXY_IP:-[\d.]+\}$/gm) ?? [];
    expect(envUses.length, 'scraper y ml-dotnet tienen que recibir TRUSTED_PROXY_IP').toBe(2);
    expect(COMPOSE).toMatch(/ipv4_address: \$\{TRUSTED_PROXY_IP:-[\d.]+\}/);

    // Y esa IP tiene que caer dentro de la subred que se le asigna.
    const proxyIp = trusted[0].match(/-([\d.]+)\}/)![1];
    const subnet = COMPOSE.match(/subnet: \$\{INTERNAL_SUBNET:-([\d.]+\/\d+)\}/)![1];
    const [net, bits] = subnet.split('/') as [string, string];
    const mask = (0xffffffff << (32 - Number(bits))) >>> 0;
    const toInt = (ip: string) => ip.split('.').reduce((acc, o) => (acc << 8) + Number(o), 0) >>> 0;
    expect(
      (toInt(proxyIp) & mask) >>> 0,
      `la IP de nginx (${proxyIp}) cae fuera de la subred ${subnet}: el proxy no ` +
        'arrancaría y el rate limit quedaría colgado de la IP de nginx.'
    ).toBe((toInt(net) & mask) >>> 0);
  });

  test('nginx deniega /scrape en vez de dejarlo pasar o devolver la SPA', async ({ request }) => {
    for (const path of ['/scrape', '/scrape/run/la_anonima']) {
      expect(upstreamFor(path), `nginx.conf proxea ${path} al scraper`).toBe('deny');
    }

    // Contra el nginx de verdad, y no solo contra el texto del archivo: si el
    // `deny` estuviera mal, `location /` respondería index.html con 200 y el
    // cliente creería que el scrape se disparó.
    const response = await request.post('/scrape/run/la_anonima', {
      headers: { 'X-Admin-Token': 'admin-token-inventado' }
    });
    expect(response.status(), `POST /scrape/run respondió ${response.status()} en el dominio público`).toBe(403);

    // Lo que llega es el 403 de nginx, no la SPA. El body de error de nginx es
    // HTML también, así que el discriminante es que no sea la app.
    const body = await response.text();
    expect(body).toContain('403');
    expect(body).not.toContain('app-root');

    // Con el token real tampoco: no es un chequeo de credenciales, es que la
    // ruta no existe para el proxy público.
    const withRealToken = await request.post('/scrape/run/la_anonima', {
      headers: { 'X-Admin-Token': process.env.ADMIN_TOKEN ?? 'sin-token' }
    });
    expect(withRealToken.status()).toBe(403);

    // El resto del API sigue proxyado: el deny no se comió nada de más.
    const chains = await request.get('/chains');
    expect(chains.status()).toBe(200);
    expect(chains.headers()['content-type']).toContain('application/json');
  });
});

/** Escapa un patrón de location para usarlo dentro de una RegExp. */
function escapeRe(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
