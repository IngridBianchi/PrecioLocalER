"""Tests del circuit breaker y BaseScraper (retries + robots)."""
import httpx
import pytest
import respx
from app.errors import CircuitOpenError, RobotsDeniedError, UpstreamError
from app.scrapers.base import BaseScraper
from app.scrapers.circuit_breaker import CircuitBreaker
from app.scrapers.robots import RobotsEnforcer


def _breaker(failure_threshold: int = 3) -> CircuitBreaker:
    return CircuitBreaker(failure_threshold=failure_threshold, reset_timeout_seconds=60)


def _scraper(client: httpx.AsyncClient, breaker: CircuitBreaker) -> BaseScraper:
    enforcer = RobotsEnforcer(
        client, user_agent="test-agent", strict_on_failure=True, cache_ttl_seconds=3600
    )
    return BaseScraper(
        client=client,
        enforcer=enforcer,
        circuit_breaker=breaker,
        retries=1,
        min_interval=0,
        backoff=0.01,
    )


@respx.mock
@pytest.mark.asyncio
async def test_retries_transient_failure() -> None:
    url = "https://cdn.test.local/aceite"
    respx.get("https://cdn.test.local/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    route = respx.get(url).mock(
        side_effect=[httpx.Response(500), httpx.Response(200, text="<html>ok</html>")]
    )
    async with httpx.AsyncClient() as client:
        scraper = _scraper(client, _breaker())
        response = await scraper.get(url)
    assert response.text == "<html>ok</html>"
    assert route.call_count == 2
    assert scraper.circuit_state == "closed"


@respx.mock
@pytest.mark.asyncio
async def test_raises_after_exhausting_retries() -> None:
    url = "https://cdn.test.local/falla"
    respx.get("https://cdn.test.local/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    respx.get(url).mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        scraper = _scraper(client, _breaker(failure_threshold=10))
        with pytest.raises(UpstreamError):
            await scraper.get(url)


@respx.mock
@pytest.mark.asyncio
async def test_robots_denial_raises_before_request() -> None:
    robots = respx.get("https://cdn.test.local/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /bloqueado\n")
    )
    url = "https://cdn.test.local/bloqueado/aceite"
    blocked = respx.get(url).mock(return_value=httpx.Response(200, text="<html/>"))
    async with httpx.AsyncClient() as client:
        scraper = _scraper(client, _breaker())
        with pytest.raises(RobotsDeniedError):
            await scraper.get(url)
    assert blocked.called is False
    assert robots.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_robots_fetch_failure_is_fail_safe() -> None:
    respx.get("https://cdn.test.local/robots.txt").mock(return_value=httpx.Response(502))
    url = "https://cdn.test.local/aceite"
    respx.get(url).mock(return_value=httpx.Response(200, text="<html/>"))
    async with httpx.AsyncClient() as client:
        scraper = _scraper(client, _breaker())
        with pytest.raises(RobotsDeniedError):
            await scraper.get(url)


@respx.mock
@pytest.mark.asyncio
async def test_does_not_follow_redirects() -> None:
    """A10: un 3xx cuenta como fallo; no seguimos redirecciones."""
    url = "https://cdn.test.local/aceite"
    redirect_url = "https://evil.test.local/aceite"
    respx.get("https://cdn.test.local/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    redirect_route = respx.get(url).mock(
        return_value=httpx.Response(302, headers={"Location": redirect_url})
    )
    foreign = respx.get(redirect_url).mock(return_value=httpx.Response(200, text="no"))
    async with httpx.AsyncClient() as client:
        scraper = _scraper(client, _breaker(failure_threshold=10))
        with pytest.raises(UpstreamError):
            await scraper.get(url)
    assert foreign.called is False
    assert redirect_route.call_count == 2  # intento + 1 retry


@respx.mock
@pytest.mark.asyncio
async def test_circuit_opens_after_threshold() -> None:
    url = "https://cdn.test.local/circuit"
    respx.get("https://cdn.test.local/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    respx.get(url).mock(return_value=httpx.Response(500))
    breaker = _breaker(failure_threshold=2)
    async with httpx.AsyncClient() as client:
        scraper = _scraper(client, breaker)
        with pytest.raises(UpstreamError):
            await scraper.get(url)
        # Segundo intento: el circuito ya está abierto, no se toca la red.
        with pytest.raises(CircuitOpenError):
            await scraper.get(url)
    assert breaker.state == "open"