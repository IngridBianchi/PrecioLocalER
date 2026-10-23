"""Endpoints públicos del scraper (contrato en docs/contracts.md §3.1)."""
from __future__ import annotations

import asyncio
import secrets
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request

from app.api.ratelimit import (
    GET_LIMIT,
    SCRAPE_CHAIN_LIMIT,
    SCRAPE_USER_LIMIT,
    chain_key,
    client_ip,
    limiter,
)
from app.errors import APIProblem
from app.models import CategoryPriceOut, ChainOut, ProductDetailOut, ProductSearchPageOut
from app.state import AppState
from app.storage.postgres import ProductRepository

router = APIRouter()
log = structlog.get_logger("app.api")


def _state(request: Request) -> AppState:
    return request.app.state.app_state


def _repo_from(state: AppState) -> ProductRepository:
    if state.repository is None:
        raise APIProblem(503, "No configurado", "Base de datos no conectada")
    return state.repository


class _ChainLock:
    """Advisory lock de Postgres para una cadena, tomado sobre la sesión.

    Semántica de `pg_try_advisory_lock`: es de **sesión**, no de transacción. Eso
    es lo que hace que el lock sobreviva a los commits intermedios del scrape
    (que son muchos: un upsert por producto) y, sobre todo, que se libere solo
    si la conexión muere — que es exactamente el caso que queremos: si el
    contenedor se mata a mitad de un scrape, la sesión cae y Postgres libera el
    lock, en vez de dejar la cadena trabada hasta que alguien reinicie a mano.

    El key es un `hashtext` del slug, para no depender de que los ids de cadena
    coincidan entre bases (en la de test son los de 002_seed, en producción
    otros) y para que el lock sea legible en `pg_locks`.

    Por eso el handle guarda la **conexión**: el lock vive en la sesión, así que
    el `fetchval` del acquire y el del unlock tienen que salir por el mismo
    socket. Usar el pool directamente (que toma y suelta una conexión por
    query) dejaría el lock tomado en una sesión que ya se cerró y el unlock
    sería un no-op silencioso: el lock quedaría tomado en una sesión muerta.
    """

    def __init__(self, conn: object, chain: str) -> None:
        self._conn = conn
        self._chain = chain
        self._held = False

    async def acquired(self) -> bool:
        self._held = bool(
            await self._conn.fetchval(  # type: ignore[attr-defined]
                "SELECT pg_try_advisory_lock(hashtext($1))", self._chain
            )
        )
        return self._held

    async def release(self) -> None:
        if self._held:
            self._held = False
            await self._conn.execute(  # type: ignore[attr-defined]
                "SELECT pg_advisory_unlock(hashtext($1))", self._chain
            )


@asynccontextmanager
async def _chain_lock(state: AppState, chain: str) -> AsyncIterator[_ChainLock]:
    """Toma una conexión del pool y la retiene durante todo el lock.

    El `async with pool.acquire()` es lo que hace que el lock sea de sesión de
    verdad: la conexión queda checked out hasta que salimos del `with`, o sea
    hasta después del unlock.
    """
    if state.pool_provider is None:
        raise APIProblem(503, "No configurado", "DB no conectada para el scrape")
    async with state.pool_provider.pool.acquire() as conn:
        lock = _ChainLock(conn, chain)
        try:
            yield lock
        finally:
            await lock.release()


def require_admin(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """Valida el token admin ANTES de que slowapi consuma el bucket de rate
    limit (así un atacante no autenticado no agota el scrape del admin).

    Audita cada intento (P0.3: "sin auditoría de intentos"). Los dos casos que
    importan para una revisión de seguridad son el token ausente y el token
    equivocado; el éxito se loguea aparte desde el endpoint, porque acá solo se
    llega cuando algo falló.

    **Nunca se loguea el valor recibido.** Solo si vino vacío o no, y cuántos
    caracteres tenía: un `logger.warning(token=x_admin_token)` convierte el log
    en el lugar donde vive el secreto, y este header es lo único que separa a
    un atacante de un scrape sobre la base de producción.
    """
    token = request.app.state.app_state.settings.admin_token
    if not token:
        raise APIProblem(501, "No implementado", "admin token no configurado (ADMIN_TOKEN)")
    expected = token.encode("utf-8")
    provided = (x_admin_token or "").encode("utf-8")
    if not secrets.compare_digest(provided, expected):
        log.warning(
            "admin_token.invalido",
            chain=request.path_params.get("chain"),
            client=client_ip(request),
            token_present=bool(provided),
            provided_len=len(provided),
        )
        raise APIProblem(401, "No autorizado", "token admin inválido")


@router.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "service": "scraper", "timestamp": time.time()}


@router.get("/ready")
async def ready(request: Request) -> dict[str, object]:
    state = _state(request)
    checks = {"database": await state.database_ready()}
    if state.redis.configured:
        checks["redis"] = await state.redis.ping()
    if all(checks.values()):
        return {"status": "ready", "checks": checks}
    raise APIProblem(503, "No listo", "hay dependencias caídas", checks=checks)


@router.get("/chains", response_model=list[ChainOut])
@limiter.limit(GET_LIMIT)
async def list_chains(request: Request) -> list[ChainOut]:
    """Cadenas activas. La web las usa para el selector de cadena (WP4)."""
    repo = _repo_from(_state(request))
    return await repo.chains()


@router.get("/products", response_model=ProductSearchPageOut)
@limiter.limit(GET_LIMIT)
async def search_products(
    request: Request,
    q: str = Query(min_length=2, max_length=80),
    chain: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> ProductSearchPageOut:
    repo = _repo_from(_state(request))
    return await repo.search_products(q.strip(), chain, limit, offset)


@router.get("/products/{product_id}", response_model=ProductDetailOut)
@limiter.limit(GET_LIMIT)
async def product_detail(
    request: Request,
    product_id: int,
) -> ProductDetailOut:
    repo = _repo_from(_state(request))
    detail = await repo.product_detail(product_id)
    if detail is None:
        raise APIProblem(404, "No encontrado", f"producto {product_id} no existe")
    return detail


@router.get("/products/{product_id}/history")
@limiter.limit(GET_LIMIT)
async def product_history(
    request: Request,
    product_id: int,
    span: str | None = Query(default="30d", pattern="^(7d|30d|90d)$"),
    chain: str | None = Query(default=None, max_length=50),
) -> list[object]:
    repo = _repo_from(_state(request))
    points = await repo.price_history(product_id, span, chain)
    return [p.model_dump(mode="json") for p in points]


@router.get("/categories/{slug}/prices", response_model=list[CategoryPriceOut])
@limiter.limit(GET_LIMIT)
async def category_prices(
    request: Request,
    slug: str,
    chain: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[CategoryPriceOut]:
    repo = _repo_from(_state(request))
    return await repo.category_prices(slug, chain, limit)


@router.post("/scrape/run/{chain}")
@limiter.limit(SCRAPE_USER_LIMIT)
@limiter.limit(SCRAPE_CHAIN_LIMIT, key_func=chain_key)
async def run_scrape(
    request: Request,
    chain: str,
    _admin: None = Depends(require_admin),
) -> dict[str, object]:
    state = _state(request)
    if chain not in state.scrapers:
        raise APIProblem(
            400,
            "Cadena inválida",
            f"cadenas soportadas: {', '.join(state.chain_slugs)}",
        )
    try:
        runner = state.runner(chain)
    except RuntimeError:
        raise APIProblem(503, "No configurado", "DB no conectada para el scrape") from None

    # Lock por cadena (P0.3). El rate limit de cadena (5/hora) frena el
    # abuso, pero no impide que dos POST seguidos se solapen: los dos pasan el
    # bucket y los dos runners escriben a la vez sobre las mismas tablas. Con
    # `ON CONFLICT` la corrupción es improbable, no imposible (el barrido de
    # SKUs desactivados de 010 mira el resultado del otro scrape).
    #
    # El lock vive en Postgres y no en memoria porque es el único estado
    # compartido que ya existe sin agregar infra: sobrevive a un restart del
    # contenedor y sirve igual si mañana hay dos réplicas.
    async with _chain_lock(state, chain) as lock:
        if not await lock.acquired():
            log.warning("scrape.lock.ocupado", chain=chain, client=client_ip(request))
            raise APIProblem(409, "En curso", f"ya hay un scrape en curso para {chain}")
        log.info("scrape.iniciado", chain=chain, client=client_ip(request))
        try:
            summary = await runner.run()
        except Exception:
            log.warning("scrape.error", chain=chain, client=client_ip(request))
            raise
        log.info(
            "scrape.terminado",
            chain=chain,
            client=client_ip(request),
            resumen=summary.model_dump(),
        )
        return summary.model_dump()


_RETRAIN_RUNNING = False


def _run_training() -> None:
    """Ejecuta el job de entrenamiento en un proceso separado."""
    global _RETRAIN_RUNNING
    try:
        subprocess.run(
            [sys.executable, "/scripts/train_forecasts.py"],
            check=False,
        )
    except Exception:
        pass
    finally:
        _RETRAIN_RUNNING = False


@router.post("/admin/retrain")
@limiter.limit(SCRAPE_USER_LIMIT)
async def retrain_models(
    request: Request,
    background_tasks: BackgroundTasks,
    _admin: None = Depends(require_admin),
) -> dict[str, object]:
    global _RETRAIN_RUNNING
    if _RETRAIN_RUNNING:
        raise APIProblem(409, "En curso", "ya hay un entrenamiento en curso")
    _RETRAIN_RUNNING = True
    background_tasks.add_task(_run_training)
    log.info("retrain.iniciado", client=client_ip(request))
    return {"status": "started"}


@router.get("/scrape")
@limiter.limit(GET_LIMIT)
async def scrape_status(request: Request) -> dict[str, object]:
    return {"chains": _state(request).chain_slugs, "status": "idle"}