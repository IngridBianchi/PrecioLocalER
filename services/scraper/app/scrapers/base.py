"""BaseScraper: HTTP con retries, backoff, throttling y robots.txt."""
from __future__ import annotations

import asyncio
import time

import httpx

from app.errors import RobotsDeniedError, UpstreamError
from app.models import RawProduct
from app.scrapers.circuit_breaker import CircuitBreaker
from app.scrapers.robots import RobotsEnforcer


class BaseScraper:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        enforcer: RobotsEnforcer,
        circuit_breaker: CircuitBreaker | None = None,
        retries: int = 3,
        backoff: float = 2.0,
        min_interval: float = 1.0,
    ) -> None:
        self._client = client
        self._enforcer = enforcer
        self._circuit = circuit_breaker or CircuitBreaker()
        self._retries = max(0, retries)
        self._backoff = backoff
        self._min_interval = min_interval
        self._last_request_at: float | None = None

    async def _throttle(self) -> None:
        now = time.monotonic()
        if self._last_request_at is not None:
            elapsed = now - self._last_request_at
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()

    async def get(self, url: str) -> httpx.Response:
        if not await self._enforcer.can_fetch(url):
            raise RobotsDeniedError(f"robots.txt impide: {url}")
        last_error: httpx.HTTPError | None = None
        for attempt in range(self._retries + 1):
            self._circuit.before()
            await self._throttle()
            try:
                # follow_redirects=False: A10 (SSRF) — no seguimos redirecciones
                # hacia otros hosts; las URLs de scraping son fijas por adaptador.
                response = await self._client.get(url, follow_redirects=False)
                response.raise_for_status()
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
                self._circuit.on_failure()
                if attempt < self._retries:
                    await asyncio.sleep(self._backoff**attempt)
                    continue
                break
            else:
                self._circuit.on_success()
                return response
        assert last_error is not None
        raise UpstreamError(f"sitio no respondió ({url}): {last_error}") from last_error

    @property
    def circuit_state(self) -> str:
        return self._circuit.state

    async def scrape_catalog(self) -> list[RawProduct]:
        """Recorre el catálogo de la cadena. Implementado por cada adaptador."""
        raise NotImplementedError