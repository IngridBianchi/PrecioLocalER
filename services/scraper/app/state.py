"""Estado compartido de la app: pool, repositorios y scrapers, creado en lifespan."""
from __future__ import annotations

import httpx

from app.config import Settings
from app.pipelines.runner import ScrapeRunner
from app.scrapers.base import BaseScraper
from app.scrapers.carrefour import CarrefourScraper
from app.scrapers.changomas import ChangomasScraper
from app.scrapers.coto_digital import CotoDigitalScraper
from app.scrapers.la_anonima import LaAnonimaScraper
from app.storage.postgres import PoolProvider, ProductRepository, RedisReadiness


class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool_provider: PoolProvider | None = None
        self.redis: RedisReadiness = RedisReadiness(settings.redis_url)
        self.repository: ProductRepository | None = None
        self.scrapers: dict[str, BaseScraper] = {}
        self._client: httpx.AsyncClient | None = None

    @property
    def chain_slugs(self) -> list[str]:
        return sorted(self.scrapers)

    async def startup(self) -> None:
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.settings.scraper_timeout_seconds,
            headers={"User-Agent": self.settings.scraper_user_agent},
        )
        self.scrapers = {
            "la_anonima": LaAnonimaScraper(client=self._client, settings=self.settings),
            "coto_digital": CotoDigitalScraper(client=self._client, settings=self.settings),
            "carrefour": CarrefourScraper(client=self._client, settings=self.settings),
            "changomas": ChangomasScraper(client=self._client, settings=self.settings),
        }
        # `cooperativa_obrera` NO tiene adaptador y no es un bug: el sitio
        # (cooperativaobrera.coop) es institucional de Joomla, sin catálogo
        # público. `/tienda` y el sitemap devuelven 500, `tienda.`/`shop.` no
        # resuelven por DNS, y robots.txt bloquea /components/, /modules/ y
        # /cache/. La venta online pasa por portal-clientes.cooperativaobrera.coop,
        # que es login de cuentas corrientes, no un catálogo. La cadena queda
        # en la tabla `chains` (aparece en GET /chains) sin price_points hasta
        # que haya una fuente pública real. Ver CONTEXTO.md §16.
        await self.redis.start()
        if self.settings.database_url:
            self.pool_provider = PoolProvider(self.settings.database_url)
            await self.pool_provider.start()
            self.repository = ProductRepository(self.pool_provider)

    async def shutdown(self) -> None:
        if self.pool_provider is not None:
            await self.pool_provider.stop()
        await self.redis.stop()
        if self._client is not None:
            await self._client.aclose()

    async def database_ready(self) -> bool:
        if self.pool_provider is None:
            return False
        try:

            async with self.pool_provider.pool.acquire() as conn:
                return bool(await conn.fetchval("SELECT 1"))
        except Exception:
            return False

    def runner(self, chain: str) -> ScrapeRunner:
        if self.repository is None:
            raise RuntimeError("DB no configurada")
        scraper = self.scrapers.get(chain)
        if scraper is None:
            raise LookupError(f"cadena '{chain}' no soportada")
        return ScrapeRunner(scraper, self.repository, chain, redis=self.redis)