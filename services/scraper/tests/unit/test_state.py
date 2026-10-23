"""Tests de AppState: fábrica de scrapers y ciclo de vida.

`app/state.py` estaba en 51% sin un solo test, y es donde se decide qué cadena
tiene adaptador. Un slug mal registrado ahí no falla en el arranque: falla en
producción, cuando alguien llama `POST /scrape/run/{chain}` y recibe 500.
"""
from __future__ import annotations

from typing import Any, cast

import httpx
import pytest
from app.config import Settings
from app.scrapers.base import BaseScraper
from app.scrapers.carrefour import CarrefourScraper
from app.scrapers.changomas import ChangomasScraper
from app.scrapers.coto_digital import CotoDigitalScraper
from app.scrapers.la_anonima import LaAnonimaScraper
from app.state import AppState


@pytest.fixture
def empty_settings() -> Settings:
    return Settings(
        app_environment="test",
        database_url="",
        redis_url="",
        scraper_min_request_interval=0,
    )


async def _started(settings: Settings) -> tuple[AppState, httpx.AsyncClient]:
    state = AppState(settings)
    await state.startup()
    assert state._client is not None
    return state, state._client


class _StubRepository:
    """Solo para que `runner()` pase el check de DB; no se usa en estos tests."""


def _with_repository(state: AppState) -> AppState:
    state.repository = cast(Any, _StubRepository())
    return state


@pytest.mark.asyncio
async def test_registers_a_scraper_for_every_scrapable_chain(empty_settings: Settings) -> None:
    """Cada cadena con adaptador conocido queda mapeada a su clase."""
    state, _ = await _started(empty_settings)
    try:
        assert state.scrapers.keys() == {"la_anonima", "coto_digital", "carrefour", "changomas"}
        assert isinstance(state.scrapers["la_anonima"], LaAnonimaScraper)
        assert isinstance(state.scrapers["coto_digital"], CotoDigitalScraper)
        assert isinstance(state.scrapers["carrefour"], CarrefourScraper)
        assert isinstance(state.scrapers["changomas"], ChangomasScraper)
    finally:
        await state.shutdown()


@pytest.mark.asyncio
async def test_every_registered_scraper_is_a_base_scraper(empty_settings: Settings) -> None:
    """Guarda contra registrar algo que no implemente el contrato."""
    state, _ = await _started(empty_settings)
    try:
        for slug, scraper in state.scrapers.items():
            assert isinstance(scraper, BaseScraper), slug
    finally:
        await state.shutdown()


@pytest.mark.asyncio
async def test_cooperativa_obrera_has_no_scraper(empty_settings: Settings) -> None:
    """La cadena existe en la tabla `chains` pero el sitio no tiene catálogo.

    Ver CONTEXTO.md §16: cooperativaobrera.coop es institucional de Joomla, sin
    tienda pública. Registrarla con un adaptadorStub sería mentir.
    """
    state, _ = await _started(empty_settings)
    try:
        assert "cooperativa_obrera" not in state.scrapers
    finally:
        await state.shutdown()


@pytest.mark.asyncio
async def test_chain_slugs_are_sorted(empty_settings: Settings) -> None:
    state, _ = await _started(empty_settings)
    try:
        assert state.chain_slugs == sorted(state.scrapers)
        assert state.chain_slugs[0] == "carrefour"
    finally:
        await state.shutdown()


@pytest.mark.asyncio
async def test_runner_returns_runner_for_registered_chain(empty_settings: Settings) -> None:
    from app.pipelines.runner import ScrapeRunner

    state, _ = await _started(empty_settings)
    _with_repository(state)
    try:
        runner = state.runner("changomas")
        assert isinstance(runner, ScrapeRunner)
    finally:
        await state.shutdown()


@pytest.mark.asyncio
async def test_runner_raises_for_unknown_chain(empty_settings: Settings) -> None:
    state, _ = await _started(empty_settings)
    _with_repository(state)
    try:
        with pytest.raises(LookupError, match="no soportada"):
            state.runner("cooperativa_obrera")
        with pytest.raises(LookupError):
            state.runner("cadena-inexistente")
    finally:
        await state.shutdown()


@pytest.mark.asyncio
async def test_runner_requires_repository(empty_settings: Settings) -> None:
    """Sin `database_url` no hay repositorio: falla claro, no en la query."""
    state, _ = await _started(empty_settings)
    try:
        assert state.repository is None
        assert state.pool_provider is None
        with pytest.raises(RuntimeError, match="DB no configurada"):
            state.runner("changomas")
    finally:
        await state.shutdown()


@pytest.mark.asyncio
async def test_database_ready_false_without_pool(empty_settings: Settings) -> None:
    state, _ = await _started(empty_settings)
    try:
        assert await state.database_ready() is False
    finally:
        await state.shutdown()


@pytest.mark.asyncio
async def test_database_ready_false_when_query_fails(empty_settings: Settings) -> None:
    """Un pool caído devuelve `False` (→ `/ready` degrade) y no una excepción."""

    class _BrokenPool:
        @property
        def pool(self) -> Any:
            return self

        def acquire(self) -> Any:
            raise RuntimeError("connection refused")

    state, _ = await _started(empty_settings)
    try:
        state.pool_provider = _BrokenPool()  # type: ignore[assignment]
        assert await state.database_ready() is False
    finally:
        state.pool_provider = None
        await state.shutdown()


@pytest.mark.asyncio
async def test_shutdown_is_safe_without_startup(empty_settings: Settings) -> None:
    """`shutdown()` sin `startup()` no debe explotar (tests, failsafe)."""
    state = AppState(empty_settings)
    await state.shutdown()


@pytest.mark.asyncio
async def test_scrapers_share_one_client(empty_settings: Settings) -> None:
    """Un solo AsyncClient para las 4 cadenas: un solo pool de conexiones."""
    state, client = await _started(empty_settings)
    try:
        clients = {
            id(scr._client) for scr in state.scrapers.values()  # noqa: SLF001
        }
        assert clients == {id(client)}
    finally:
        await state.shutdown()


@pytest.mark.asyncio
async def test_robots_enforcer_created_per_scraper(empty_settings: Settings) -> None:
    """Cada adaptador tiene su propio enforcer con su propio UA/cache."""
    state, _ = await _started(empty_settings)
    try:
        enforcers = {
            id(scr._enforcer) for scr in state.scrapers.values()  # noqa: SLF001
        }
        assert len(enforcers) == len(state.scrapers)
    finally:
        await state.shutdown()
