"""FastAPI app del scraper PrecioLocal ER (Fase 1)."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.api.ratelimit import limiter
from app.api.router import router
from app.config import Settings, get_settings
from app.errors import (
    APIProblem,
    CircuitOpenError,
    RobotsDeniedError,
    ScraperError,
    UpstreamError,
    problem,
)
from app.ml.api import router as ml_router
from app.observability import (
    CorrelationIdMiddleware,
    _setup_prometheus,
    _setup_structured_logging,
    setup_observability,
)
from app.state import AppState


def _configure_logging(level: str) -> None:
    level_name = getattr(logging, level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_name),
    )


def _build_app(settings: Settings) -> FastAPI:
    _configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = AppState(settings)
        await state.startup()
        app.state.app_state = state
        # Structured logging / tracing se configura en startup (no rompe tests).
        _setup_structured_logging(settings)
        yield
        await state.shutdown()

    # En producción se cierra la superficie de documentación por completo:
    # /docs, /redoc y /openapi.json exponen el contrato y la estructura
    # interna de la API. Sin esto, /docs podía cerrarse pero /openapi.json
    # seguía publicado (docs/security.md §5).
    docs_enabled = settings.app_environment != "production"
    application = FastAPI(
        title="PrecioLocal ER — Scraper",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    # Correlation ID middleware for distributed tracing
    application.add_middleware(CorrelationIdMiddleware)

    @application.exception_handler(APIProblem)
    async def _api_problem(_req: Request, exc: APIProblem) -> JSONResponse:
        return JSONResponse(
            status_code=exc.payload["status"],
            content=exc.payload,
            media_type="application/problem+json",
        )

    @application.exception_handler(RobotsDeniedError)
    async def _robots(_req: Request, exc: RobotsDeniedError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content=problem(403, "robots.txt", str(exc)),
            media_type="application/problem+json",
        )

    @application.exception_handler(CircuitOpenError)
    async def _circuit(_req: Request, exc: CircuitOpenError) -> JSONResponse:
        retry_after = settings.scraper_circuit_reset_timeout
        return JSONResponse(
            status_code=503,
            content=problem(503, "Circuit open", str(exc), retry_after=retry_after),
            media_type="application/problem+json",
        )

    @application.exception_handler(UpstreamError)
    @application.exception_handler(ScraperError)
    async def _upstream(_req: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content=problem(502, "Origen no disponible", str(exc)),
            media_type="application/problem+json",
        )

    @application.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=problem(
                422,
                "Validación fallida",
                "parámetros inválidos",
                errors=exc.errors(),
            ),
            media_type="application/problem+json",
        )

    @application.exception_handler(RateLimitExceeded)
    async def _rate_limited(_req: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content=problem(
                429,
                "Demasiadas peticiones",
                "límite de peticiones alcanzado, reintente más tarde",
                retry_after=exc.retry_after if hasattr(exc, "retry_after") else None,
            ),
            media_type="application/problem+json",
            headers={"Retry-After": str(getattr(exc, "retry_after", 60))},
        )

    @application.exception_handler(Exception)
    async def _unhandled(req: Request, exc: Exception) -> JSONResponse:
        log = structlog.get_logger("app")
        log.exception("unhandled_exception", path=req.url.path)
        return JSONResponse(
            status_code=500,
            content=problem(500, "Error interno", "ocurrió un error inesperado"),
            media_type="application/problem+json",
        )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    application.state.limiter = limiter
    application.include_router(router)
    application.include_router(ml_router)
    # Prometheus metrics must be registered before the app starts serving.
    _setup_prometheus(application)
    return application


settings: Settings = get_settings()
app: FastAPI = _build_app(settings)