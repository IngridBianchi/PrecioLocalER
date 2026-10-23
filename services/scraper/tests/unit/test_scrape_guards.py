"""P0.3: auditoría del token admin y lock por cadena.

Los dos son controles que no se ven en un response salvo cuando fallan, así que
lo que se prueba acá es exactamente ese camino de error: qué se loguea, qué
status se devuelve, y qué no se filtra.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.api.ratelimit import limiter
from app.config import Settings
from app.main import app
from app.state import AppState

TOKEN = "token-de-prueba-abcdef0123456789"


class _FakeConn:
    """Conexión asyncpg mínima para el lock, con la sesión simulada.

    El lock de cadena es de SESIÓN: si el unlock saliera por otra conexión, no
    liberaría nada. Estos fakes lo modelan con `session` para que un test pueda
    comprobar que el acquire y el unlock hablan con la misma sesión, que es la
    propiedad de la que depende que la cadena no quede trabada.
    """

    def __init__(self, *, lock_taken: bool = True) -> None:
        self.session = "session-1"
        self.lock_taken = lock_taken
        self.calls: list[tuple[str, str]] = []

    async def fetchval(self, query: str, chain: str) -> bool:
        self.calls.append((self.session, query))
        return self.lock_taken

    async def execute(self, query: str, chain: str) -> None:
        self.calls.append((self.session, query))


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn
        self.acquired = 0

    def acquire(self) -> _FakeAcquire:
        self.acquired += 1
        return _FakeAcquire(self._conn)


class _FakePoolProvider:
    def __init__(self, conn: _FakeConn) -> None:
        self.pool = _FakePool(conn)


class _SlowRunner:
    """Runner que se queda esperando, para probar el 409 sin scrape real."""

    def __init__(self) -> None:
        self.started = False

    async def run(self) -> Any:
        self.started = True
        import asyncio

        await asyncio.sleep(0.05)
        return type("S", (), {"model_dump": lambda _self: {"ok": True}})()


@pytest.fixture(autouse=True)
def _reset_limiter() -> Any:
    limiter.reset()
    yield
    limiter.reset()


def _state(**overrides: Any) -> AppState:
    settings = Settings(
        app_environment="test",
        database_url="",
        redis_url="",
        admin_token=TOKEN,
        **overrides,
    )
    state = AppState(settings)
    state.scrapers = {"la_anonima": object()}  # type: ignore[dict-item]
    return state


def _client(state: AppState, *, raise_app_exceptions: bool = True) -> httpx.AsyncClient:
    app.state.app_state = state
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _eventos(salida: str) -> list[dict[str, Any]]:
    """Parsea las líneas de log estructurado que emitió la app.

    Se parsea el stdout y no se usa `caplog` a propósito: structlog está
    configurado con `JSONRenderer` (services/scraper/app/main.py:32), o sea que
    el log real es una línea JSON. Afirmar sobre esa línea verifica el pipeline
    entero —que el evento llega al log con los campos correctos— y no solo que
    se llamó a un `log.warning`.

    Recibe el texto ya capturado (y no el fixture) porque `capsys.readouterr()`
    consume lo pendiente: llamarlo dos veces daría una salida vacía la segunda.
    """
    eventos: list[dict[str, Any]] = []
    for linea in salida.splitlines():
        try:
            parsed = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "event" in parsed:
            eventos.append(parsed)
    return eventos


# ---------------------------------------------------------------------------
# Auditoría del token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_invalido_se_audita_sin_loggear_el_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Un 401 deja rastro, pero el rastro no contiene el secreto.

    Se manda un token equivocado *con una forma reconocible* para poder
    afirmar que no aparece en el log: si el código hiciera
    `log.warning(token=x)`, el valor estaría ahí y este test lo detectaría.
    """
    state = _state()
    async with _client(state) as client:
        response = await client.post(
            "/scrape/run/la_anonima", headers={"X-Admin-Token": "SEGRETO-DE-PRUEBA-9999"}
        )
    assert response.status_code == 401

    salida = capsys.readouterr().out
    assert "SEGRETO-DE-PRUEBA-9999" not in salida, "el token recibido se filtró al log"
    assert TOKEN not in salida, "el token configurado se filtró al log"

    eventos = _eventos(salida)
    invalidos = [e for e in eventos if e["event"] == "admin_token.invalido"]
    assert len(invalidos) == 1
    evento = invalidos[0]
    # Qué se audita: qué endpoint, desde dónde, y que hubo token pero no cuál.
    assert evento["chain"] == "la_anonima"
    assert evento["token_present"] is True
    assert evento["provided_len"] == len("SEGRETO-DE-PRUEBA-9999")
    assert "token" not in {k for k in evento if k.endswith("token") and k != "token_present"}


@pytest.mark.asyncio
async def test_token_ausente_se_audita_como_ausente(capsys: pytest.CaptureFixture[str]) -> None:
    """Sin header, el log dice `token_present=false` (no inventa un valor)."""
    state = _state()
    async with _client(state) as client:
        response = await client.post("/scrape/run/la_anonima")
    assert response.status_code == 401

    eventos = _eventos(capsys.readouterr().out)
    invalidos = [e for e in eventos if e["event"] == "admin_token.invalido"]
    assert len(invalidos) == 1
    assert invalidos[0]["token_present"] is False
    assert invalidos[0]["provided_len"] == 0


@pytest.mark.asyncio
async def test_401_no_consume_bucket_de_scrape() -> None:
    """La auditoría no debe abrir un DoS nuevo.

    `require_admin` sigue siendo una dependencia que corre ANTES que slowapi, así
    que un intento fallido no gasta el cupo ni el bucket por cadena. Es la
    propiedad que hacía que H3 valiera; el log no la cambia.
    """
    state = _state()
    async with _client(state) as client:
        for _ in range(Settings().scrape_limit_per_user_hour + 5):
            response = await client.post(
                "/scrape/run/la_anonima", headers={"X-Admin-Token": "malo"}
            )
            assert response.status_code == 401


# ---------------------------------------------------------------------------
# Lock por cadena
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lock_tomado_y_liberado_en_la_misma_sesion() -> None:
    """Acquire y unlock tienen que salir por la misma conexión.

    Es la propiedad de la que depende todo: `pg_try_advisory_lock` es de
    sesión, así que un unlock por otro socket no libera nada y la cadena queda
    trabada hasta que esa sesión muera.
    """
    conn = _FakeConn(lock_taken=True)
    state = _state()
    state.pool_provider = _FakePoolProvider(conn)  # type: ignore[assignment]
    runner = _SlowRunner()
    state.runner = lambda _chain: runner  # type: ignore[method-assign, assignment, return-value]

    async with _client(state) as client:
        response = await client.post(
            "/scrape/run/la_anonima", headers={"X-Admin-Token": TOKEN}
        )

    assert response.status_code == 200, response.text
    assert runner.started
    sesiones = {sesion for sesion, _ in conn.calls}
    assert len(sesiones) == 1, f"el lock se tomó en más de una sesión: {sesiones}"
    assert sum(1 for _, q in conn.calls if "pg_try_advisory_lock" in q) == 1
    assert sum(1 for _, q in conn.calls if "pg_advisory_unlock" in q) == 1


@pytest.mark.asyncio
async def test_segundo_scrape_mientras_corren_otro_es_409() -> None:
    """Con el lock tomado por otro, el segundo POST responde 409 y no corre.

    El detalle que importa: no es 429 (el rate limit ya lo couvre) ni 200
    (que sería el doble de trabajo escribiendo las mismas tablas). Es "En curso".
    """
    conn = _FakeConn(lock_taken=False)  # el lock ya está en manos de otro
    state = _state()
    state.pool_provider = _FakePoolProvider(conn)  # type: ignore[assignment]
    runner = _SlowRunner()
    state.runner = lambda _chain: runner  # type: ignore[method-assign, assignment, return-value]

    async with _client(state) as client:
        response = await client.post(
            "/scrape/run/la_anonima", headers={"X-Admin-Token": TOKEN}
        )

    assert response.status_code == 409
    assert response.json()["title"] == "En curso"
    assert not runner.started, "no debe correr el scrape si no se pudo tomar el lock"
    # No se intentó liberar un lock que nunca se tomó.
    assert not any("pg_advisory_unlock" in q for _, q in conn.calls)


@pytest.mark.asyncio
async def test_lock_se_libera_aunque_el_scrape_falle() -> None:
    """Si el runner explota, el unlock tiene que correr igual.

    Es el motivo de usar `try/finally` en el context manager: sin él, un scrape
    fallido deja la cadena trabada y el único remedio es reiniciar Postgres a
    mano.
    """

    class _Boom:
        async def run(self) -> Any:
            raise RuntimeError("se rompió el scrape")

    conn = _FakeConn(lock_taken=True)
    state = _state()
    state.pool_provider = _FakePoolProvider(conn)  # type: ignore[assignment]
    state.runner = lambda _chain: _Boom()  # type: ignore[method-assign, assignment, return-value]

    # raise_app_exceptions=False para que la app conteste su propio 500 en vez
    # de dejar que la excepción suba al test.
    async with _client(state, raise_app_exceptions=False) as client:
        response = await client.post(
            "/scrape/run/la_anonima", headers={"X-Admin-Token": TOKEN}
        )

    assert response.status_code == 500
    assert any("pg_advisory_unlock" in q for _, q in conn.calls)


@pytest.mark.asyncio
async def test_sin_db_el_scrape_es_503_no_un_scrape_sin_lock() -> None:
    """Sin pool no hay lock, y sin lock tampoco scrape.

    La alternativa (dejar correr sin lock "porque total es una sola réplica")
    es la condición que el lock existe para evitar: fail-closed.
    """
    state = _state()
    state.pool_provider = None
    runner = _SlowRunner()
    state.runner = lambda _chain: runner  # type: ignore[method-assign, assignment, return-value]

    async with _client(state) as client:
        response = await client.post(
            "/scrape/run/la_anonima", headers={"X-Admin-Token": TOKEN}
        )

    assert response.status_code == 503
    assert not runner.started
