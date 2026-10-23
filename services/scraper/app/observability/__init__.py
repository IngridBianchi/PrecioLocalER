"""Observability setup for PrecioLocal Scraper.

Configures OpenTelemetry tracing, Prometheus metrics, and structured logging
with correlation IDs for end-to-end observability.
"""

import logging
import os
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.typing import Processor

# Optional instrumentations: only available if the
# opentelemetry-instrumentation-* packages are installed.
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    _HAS_FASTAPI_INSTRUMENTOR = True
except ImportError:
    FastAPIInstrumentor = None
    _HAS_FASTAPI_INSTRUMENTOR = False

try:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    _HAS_HTTPX_INSTRUMENTOR = True
except ImportError:
    HTTPXClientInstrumentor = None
    _HAS_HTTPX_INSTRUMENTOR = False

try:
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
    _HAS_ASYNCPG_INSTRUMENTOR = True
except ImportError:
    AsyncPGInstrumentor = None
    _HAS_ASYNCPG_INSTRUMENTOR = False

try:
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    _HAS_REDIS_INSTRUMENTOR = True
except ImportError:
    RedisInstrumentor = None
    _HAS_REDIS_INSTRUMENTOR = False

try:
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    _HAS_LOGGING_INSTRUMENTOR = True
except ImportError:
    LoggingInstrumentor = None
    _HAS_LOGGING_INSTRUMENTOR = False

from app.config import Settings, get_settings

# Context variable for correlation ID propagation
correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)

# Global tracer provider
_tracer_provider: TracerProvider | None = None


def setup_observability(app: Any = None) -> None:
    """Initialize OpenTelemetry, Prometheus metrics, and structured logging."""
    global _tracer_provider
    settings = get_settings()

    # Service metadata
    service_name = "preciolocal-scraper"
    service_version = os.getenv("SERVICE_VERSION", "0.1.0")

    # Only setup OTel if endpoint is configured (production/staging)
    otel_endpoint = settings.otel_exporter_otlp_endpoint
    if otel_endpoint:
        _setup_tracing(service_name, service_version, otel_endpoint)
        _instrument_libraries()

    # Always setup Prometheus metrics (exposed at /metrics)
    if app:
        _setup_prometheus(app)

    # Setup structured logging with correlation IDs
    _setup_structured_logging(settings)

    # Configure propagators for distributed tracing
    trace.set_span_in_context(trace.get_current_span())


def _setup_tracing(service_name: str, service_version: str, otel_endpoint: str) -> None:
    """Configure OpenTelemetry tracer provider with OTLP exporter."""
    global _tracer_provider

    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: service_version,
        "deployment.environment": os.getenv("APP_ENVIRONMENT", "development"),
    })

    _tracer_provider = TracerProvider(resource=resource)

    # OTLP HTTP exporter
    otlp_exporter = OTLPSpanExporter(
        endpoint=f"{otel_endpoint.rstrip('/')}/v1/traces",
        headers=_parse_otel_headers(os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")),
    )

    _tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    trace.set_tracer_provider(_tracer_provider)


def _parse_otel_headers(headers_str: str) -> dict[str, str]:
    """Parse OTEL_EXPORTER_OTLP_HEADERS string into dict."""
    if not headers_str:
        return {}
    headers: dict[str, str] = {}
    for pair in headers_str.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            headers[k.strip()] = v.strip()
    return headers


def _instrument_libraries() -> None:
    """Auto-instrument common libraries for distributed tracing."""
    if _HAS_FASTAPI_INSTRUMENTOR and FastAPIInstrumentor:
        FastAPIInstrumentor.instrument()
    if _HAS_HTTPX_INSTRUMENTOR and HTTPXClientInstrumentor:
        HTTPXClientInstrumentor.instrument()
    if _HAS_ASYNCPG_INSTRUMENTOR and AsyncPGInstrumentor:
        AsyncPGInstrumentor.instrument()
    if _HAS_REDIS_INSTRUMENTOR and RedisInstrumentor:
        RedisInstrumentor.instrument()
    if _HAS_LOGGING_INSTRUMENTOR and LoggingInstrumentor:
        LoggingInstrumentor.instrument(set_logging_format=True)


def _setup_prometheus(app: Any) -> None:
    """Setup Prometheus metrics endpoint at /metrics."""
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_respect_env_var=True,
        should_instrument_requests_inprogress=True,
        excluded_handlers=["/health", "/ready", "/metrics"],
        env_var_name="ENABLE_METRICS",
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


def _setup_structured_logging(settings: Settings) -> None:
    """Configure structlog with correlation ID injection."""
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.CallsiteParameterAdder([
            structlog.processors.CallsiteParameter.FUNC_NAME,
            structlog.processors.CallsiteParameter.LINENO,
        ]),
        _add_correlation_id,
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.app_environment == "production":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to use structlog
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )


def _add_correlation_id(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Add correlation ID and user ID from contextvars to log entries."""
    corr_id = correlation_id_var.get()
    if corr_id:
        event_dict["correlation_id"] = corr_id

    user_id = user_id_var.get()
    if user_id:
        event_dict["user_id"] = user_id

    return event_dict


def get_tracer(name: str = "preciolocal.scraper") -> trace.Tracer:
    """Get a tracer instance."""
    return trace.get_tracer_provider().get_tracer(name)


def set_correlation_id(corr_id: str) -> None:
    """Set correlation ID in context for current request."""
    correlation_id_var.set(corr_id)


def get_correlation_id() -> str | None:
    """Get current correlation ID from context."""
    return correlation_id_var.get()


def set_user_id(user_id: str) -> None:
    """Set user ID in context for current request."""
    user_id_var.set(user_id)


def get_user_id() -> str | None:
    """Get current user ID from context."""
    return user_id_var.get()


def inject_trace_context(headers: dict[str, str]) -> dict[str, str]:
    """Inject trace context into headers for outgoing requests."""
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    W3CBaggagePropagator().inject(carrier)
    headers.update(carrier)
    return headers


def extract_trace_context(headers: dict[str, str]) -> None:
    """Extract trace context from incoming headers."""
    TraceContextTextMapPropagator().extract(headers)
    W3CBaggagePropagator().extract(headers)


class CorrelationIdMiddleware:
    """FastAPI middleware to extract/inject correlation IDs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        # Extract correlation ID from incoming headers
        corr_id = None
        for k, v in headers:
            if k.decode().lower() in ("x-correlation-id", "x-request-id"):
                corr_id = v.decode()
                break

        if not corr_id:
            import uuid
            corr_id = str(uuid.uuid4())

        # Set in contextvar
        token = correlation_id_var.set(corr_id)

        # Wrap send to add correlation ID to response headers
        async def send_with_correlation(message: Message) -> None:
            if message["type"] == "http.response.start":
                resp_headers = list(message.get("headers", []))
                resp_headers.append((b"x-correlation-id", corr_id.encode()))
                message["headers"] = resp_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_correlation)
        finally:
            correlation_id_var.reset(token)