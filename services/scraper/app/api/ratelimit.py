"""Rate limiting global (slowapi) para el API.

Checklist de seguridad (docs/security.md §3): límites derivados de
``Settings`` para poder ajustarlos en tests/entornos.

Hardening (2026-09-24, WP1-H3):

- Las claves de bucket son SIEMPRE la **IP del cliente** (``client_ip``),
  NUNCA el header ``X-Admin-Token`` (controlable por el atacante: rotar
  bytes = bucket nuevo → DoS al límite del admin).
- Para el bucket "por cadena" se añade ``chain`` como componente de la clave,
  de modo que cada IP tiene un contador independiente por cadena.
- El límite ``SCRAPE_*`` se consume únicamente cuando la request pasó el
  admin (fail-closed en ``require_admin``, ver router.py). Las requests
  no autenticadas (501) NO consumen buckets → no se puede agotar el
  presupuesto del admin desde fuera.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import Settings, get_settings

settings: Settings = get_settings()

GET_LIMIT = f"{settings.rate_limit_requests_per_minute}/minute"
SCRAPE_USER_LIMIT = f"{settings.scrape_limit_per_user_hour}/hour"
SCRAPE_CHAIN_LIMIT = f"{settings.scrape_limit_per_chain_hour}/hour"


def client_ip(request: Request) -> str:
    """IP del cliente, ya resuelta por el servidor.

    Uvicorn aplica ``X-Forwarded-For`` **solo** si el peer figura en
    ``--forwarded-allow-ips`` (la IP fija de nginx) y deja el resultado en
    ``request.client.host``. Esa decisión ya está tomada: no hay que repetirla
    ni adivinarla leyendo el header crudo.

    Antes se leía el primer valor del ``X-Forwarded-For`` de la request. Nginx
    sobreescribe esa cabecera (``$remote_addr``, no ``$proxy_add_x_forwarded_for``),
    así que a través del proxy el valor era correcto —pero el puerto 8000
    también está publicado en 127.0.0.1 y, en cualquier escenario futuro, un
    cliente podía mandar ``X-Forwarded-For: <IP-falsa>`` y quedarse con un
    bucket de rate limit nuevo por cada valor inventado. Leyendo
    ``request.client.host``, un peer no confiable no puede abrir un bucket
    nuevo por cada IP que se le ocurra.

    Ver web/docker/nginx.conf y services/scraper/Dockerfile.
    """
    return get_remote_address(request)


def chain_key(request: Request) -> str:
    """Bucket por (IP, cadena): cada IP tiene un contador independiente por
    cadena scrapeada, sin depender de ningún header controlable."""
    chain = request.path_params.get("chain", "")
    return f"{client_ip(request)}:{chain}"


def get_redis_password() -> str:
    """Credencial Redis opcional (REDIS_PASSWORD) para el storage de slowapi."""
    return settings.redis_password or ""


def _build_storage_uri() -> str:
    """URI de storage para slowapi.

    Sin ``REDIS_URL`` → memoria (tests/dev). Con ``REDIS_URL`` → Redis en la
    base 3 (DB dedicada al rate limiting) incluyendo la credencial, sea que
    venga en la propia URL (``redis://user:pass@host``) o separada en
    ``REDIS_PASSWORD``.
    """
    if not settings.redis_url:
        return "memory://"
    base = settings.redis_url.rstrip("/")
    scheme, _, remainder = base.partition("://")
    if not remainder:
        return "memory://"
    host = remainder.rsplit("/", 1)[0] if "/" in remainder else remainder
    if "@" in host:
        return f"{scheme}://{host}/3"
    password = get_redis_password()
    credentials = f":{quote(password, safe='')}@" if password else ""
    return f"{scheme}://{credentials}{host}/3"


limiter = Limiter(
    key_func=client_ip,
    storage_uri=_build_storage_uri(),
    # bucket por endpoint (función) y no por URL: el límite "por usuario" del
    # scrape acumula todas las cadenas en un solo bucket por IP.
    key_style="endpoint",
)
