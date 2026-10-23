"""Tests del matcher de robots.txt (RFC 9309).

Cada test de este archivo es una regla de la spec o un caso donde la stdlib
`urllib.robotparser` da un veredicto incorrecto. Ver el docstring de
`app/scrapers/robots.py`.
"""
from __future__ import annotations

import httpx
import pytest
import respx
from app.scrapers.robots import RobotsEnforcer


def _enforcer(client: httpx.AsyncClient, **kwargs: object) -> RobotsEnforcer:
    defaults: dict[str, object] = {
        "user_agent": "PrecioLocal/1.0",
        "strict_on_failure": True,
        "cache_ttl_seconds": 3600,
    }
    defaults.update(kwargs)
    return RobotsEnforcer(client, **defaults)  # type: ignore[arg-type]


def _mock_robots(origin: str, text: str) -> None:
    respx.get(f"{origin}/robots.txt").mock(return_value=httpx.Response(200, text=text))


# ---------------------------------------------------------------- parser


def test_longest_match_wins_over_leading_allow() -> None:
    """Regresión: con la stdlib `Allow: /` + `Disallow: /buscar` devolvía True."""
    text = "User-agent: *\nAllow: /\nDisallow: /buscar\n"
    assert _allows(text, "https://x.local/buscar?q=aceite") is False
    assert _allows(text, "https://x.local/buscar") is False
    assert _allows(text, "https://x.local/almacen/n1_1/") is True


def test_prefix_match_is_character_exact() -> None:
    """`/buscar` no bloquea `/buscapagina`: el match es por prefijo exacto."""
    text = "User-agent: *\nDisallow: /buscar\n"
    assert _allows(text, "https://x.local/buscar/pagina") is False
    assert _allows(text, "https://x.local/buscapagina") is True


def test_allow_wins_on_equal_specificity() -> None:
    """RFC 9309 §2.2.2: empate de longitud -> gana `Allow`."""
    text = "User-agent: *\nDisallow: /carrito\nAllow: /carrito\n"
    assert _allows(text, "https://x.local/carrito") is True


def test_wildcard_matches_any_sequence() -> None:
    text = "User-agent: *\nDisallow: /img/*\n"
    assert _allows(text, "https://x.local/img/logo.png") is False
    assert _allows(text, "https://x.local/img/a/b/c.jpg") is False
    assert _allows(text, "https://x.local/images/logo.png") is True


def test_dollar_anchor_end_of_url() -> None:
    """`$` ancla al final de path+query, así que `?x=1` se escapa (igual que
    en Google: `/*.php$` no bloquea `/file.php?param`)."""
    text = "User-agent: *\nDisallow: /*.pdf$\n"
    assert _allows(text, "https://x.local/docs/manual.pdf") is False
    assert _allows(text, "https://x.local/docs/manual.pdf?descarga=1") is True
    assert _allows(text, "https://x.local/docs/manual.pdf.html") is True
    assert _allows(text, "https://x.local/docs/manual.pdf") is False


def test_query_string_is_part_of_the_match() -> None:
    """Las reglas matchean path + query, no sólo el path."""
    text = "User-agent: *\nDisallow: /api?key=\n"
    assert _allows(text, "https://x.local/api?key=secreto") is False
    assert _allows(text, "https://x.local/api?page=1") is True


def test_empty_disallow_allows_everything() -> None:
    text = "User-agent: *\nDisallow:\n"
    assert _allows(text, "https://x.local/cualquier/cosa") is True


def test_specific_user_agent_group_wins_over_wildcard() -> None:
    text = """
User-agent: *
Disallow: /

User-agent: PrecioLocal
Disallow: /privado
"""
    assert _allows(text, "https://x.local/cualquiera") is True
    assert _allows(text, "https://x.local/privado/datos") is False


def test_blank_line_separates_groups() -> None:
    text = """
User-agent: malo
Disallow: /

User-agent: *
Allow: /
"""
    assert _allows(text, "https://x.local/todo") is True


def test_comments_are_ignored() -> None:
    text = "# reglas\nUser-agent: *  # todos\nDisallow: /x # bloqueado\n"
    assert _allows(text, "https://x.local/x/y") is False
    assert _allows(text, "https://x.local/y") is True


def test_sitemap_directive_does_not_create_a_group() -> None:
    text = "Sitemap: https://x.local/sitemap.xml\nUser-agent: *\nDisallow: /x\n"
    assert _allows(text, "https://x.local/x") is False
    assert _allows(text, "https://x.local/y") is True


def test_malformed_lines_are_skipped() -> None:
    text = "basura\nUser-agent: *\nDisallow\nDisallow: /x\n"
    assert _allows(text, "https://x.local/x") is False


# ---------------------------------------------------------------- enforcer


@respx.mock
@pytest.mark.asyncio
async def test_can_fetch_uses_cache_for_the_same_origin() -> None:
    robots = respx.get("https://x.local/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    async with httpx.AsyncClient() as client:
        enforcer = _enforcer(client)
        assert await enforcer.can_fetch("https://x.local/a") is True
        assert await enforcer.can_fetch("https://x.local/b") is True
    assert robots.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_404_means_no_restrictions() -> None:
    """RFC 9309 §2.3.1.2: 4xx en robots.txt = sin restricciones."""
    respx.get("https://x.local/robots.txt").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        enforcer = _enforcer(client)
        assert await enforcer.can_fetch("https://x.local/lo-que-sea") is True


@respx.mock
@pytest.mark.asyncio
async def test_403_on_robots_is_fail_safe() -> None:
    """403 es "no podés leer las reglas": con `strict` no se scrapea."""
    respx.get("https://x.local/robots.txt").mock(return_value=httpx.Response(403))
    async with httpx.AsyncClient() as client:
        enforcer = _enforcer(client)
        assert await enforcer.can_fetch("https://x.local/a") is False


@respx.mock
@pytest.mark.asyncio
async def test_network_error_with_strict_false_allows() -> None:
    respx.get("https://x.local/robots.txt").mock(side_effect=httpx.ConnectError("boom"))
    async with httpx.AsyncClient() as client:
        enforcer = _enforcer(client, strict_on_failure=False)
        assert await enforcer.can_fetch("https://x.local/a") is True


@respx.mock
@pytest.mark.asyncio
async def test_denial_is_cached_after_failure() -> None:
    """Un fallo no debe reintentar robots.txt en cada request."""
    robots = respx.get("https://x.local/robots.txt").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        enforcer = _enforcer(client)
        assert await enforcer.can_fetch("https://x.local/a") is False
        assert await enforcer.can_fetch("https://x.local/b") is False
    assert robots.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_robots_request_sends_configured_user_agent() -> None:
    """Sin el UA configurado, sitios con bot-protection responden 403 al robots.

    Regresión real: `www.laanonima.com.ar/robots.txt` devuelve 403 si el request
    no lleva un UA de navegador; el fail-safe bloqueaba la cadena entera.
    """
    route = respx.get("https://x.local/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    async with httpx.AsyncClient() as client:
        enforcer = _enforcer(client, user_agent="PrecioLocal/2.0")
        assert await enforcer.can_fetch("https://x.local/a") is True

    assert route.calls[0].request.headers["User-Agent"] == "PrecioLocal/2.0"


@respx.mock
@pytest.mark.asyncio
async def test_robots_redirect_is_followed() -> None:
    """`www.cotodigital.com.ar/robots.txt` → 301 → `www.coto.com.ar/robots.txt`.

    Sin seguir la redirección se parsearía el HTML de la redirección y se
    concluiría "no hay reglas": un bypass silencioso de robots.txt.
    """
    respx.get("https://www.cotodigital.com.ar/robots.txt").mock(
        return_value=httpx.Response(
            301, headers={"Location": "https://www.coto.com.ar/robots.txt"}
        )
    )
    respx.get("https://www.coto.com.ar/robots.txt").mock(
        return_value=httpx.Response(
            200, text="User-agent: *\nDisallow: /privado\n"
        )
    )
    async with httpx.AsyncClient() as client:
        enforcer = _enforcer(client)
        assert await enforcer.can_fetch("https://www.cotodigital.com.ar/privado/x") is False
        assert await enforcer.can_fetch("https://www.cotodigital.com.ar/publico") is True


@respx.mock
@pytest.mark.asyncio
async def test_alias_governs_another_origin() -> None:
    """`api.coto.com.ar` sin robots propio -> rige el del sitio principal."""
    _mock_robots("https://www.cotodigital.com.ar", "User-agent: *\nDisallow: /api/\n")
    async with httpx.AsyncClient() as client:
        enforcer = _enforcer(
            client,
            aliases={"https://api.coto.com.ar": "https://www.cotodigital.com.ar"},
        )
        assert await enforcer.can_fetch("https://api.coto.com.ar/api/v1/x") is False
        assert await enforcer.can_fetch("https://api.coto.com.ar/otra") is True


@respx.mock
@pytest.mark.asyncio
async def test_expired_cache_refetches_robots() -> None:
    robots = respx.get("https://x.local/robots.txt").mock(
        side_effect=[
            httpx.Response(200, text="User-agent: *\nDisallow: /x\n"),
            httpx.Response(200, text="User-agent: *\nAllow: /\n"),
        ]
    )
    async with httpx.AsyncClient() as client:
        enforcer = _enforcer(client, cache_ttl_seconds=0)
        assert await enforcer.can_fetch("https://x.local/x") is False
        assert await enforcer.can_fetch("https://x.local/x") is True
    assert robots.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_origin_is_per_host_and_scheme() -> None:
    """El cache es por origin: `http` y `https` no comparten reglas."""
    _mock_robots("https://x.local", "User-agent: *\nDisallow: /x\n")
    _mock_robots("http://x.local", "User-agent: *\nAllow: /\n")
    async with httpx.AsyncClient() as client:
        enforcer = _enforcer(client)
        assert await enforcer.can_fetch("https://x.local/x") is False
        assert await enforcer.can_fetch("http://x.local/x") is True


def _allows(text: str, url: str) -> bool:
    """Atajo sincrónico para las reglas puras (sin red)."""
    from app.scrapers.robots import _allows as allows
    from app.scrapers.robots import _parse, _target

    return allows(_parse(text), "PrecioLocal/1.0", _target(url))
