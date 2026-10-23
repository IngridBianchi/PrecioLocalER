"""Errores de dominio + errores RFC 7807 (application/problem+json)."""
from typing import Any


class ScraperError(Exception):
    """Error base del scraper."""


class RobotsDeniedError(ScraperError):
    """robots.txt impide el acceso a la URL."""


class CircuitOpenError(ScraperError):
    """El circuit breaker está abierto; no se hacen requests."""


class UpstreamError(ScraperError):
    """Error de red / HTTP del sitio de origen."""


class APIProblem(ScraperError):
    """Respuesta de error RFC 7807 controlada (raise para el handler de FastAPI)."""

    def __init__(
        self, status: int, title: str, detail: str, instance: str | None = None, **extra: Any
    ) -> None:
        super().__init__(detail)
        self.payload = problem(status, title, detail, instance=instance, **extra)


def problem(
    status: int, title: str, detail: str, instance: str | None = None, **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": title,
        "status": status,
        "detail": detail,
    }
    if instance:
        body["instance"] = instance
    body.update(extra)
    return body