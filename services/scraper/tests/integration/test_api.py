"""Tests de integración de la API (repository fake, sin DB).

Verifican el contrato público de docs/contracts.md §3.1.
"""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from app.api.ratelimit import limiter
from app.config import Settings
from app.main import app
from app.models import ChainOut, PricePointOut, ProductSearchOut, ProductSearchPageOut
from app.state import AppState


class FakeRepository:
    def __init__(self) -> None:
        self.products = [
            ProductSearchOut(
                id=1,
                name="Aceite de Girasol La Anónima x 900 cc.",
                normalized_name="aceite de girasol la anonima 900cc",
                brand="La Anonima",
                category="Aceites",
                category_slug="aceites",
                size_quantity=0.9,
                size_unit="cc",
                chain="la_anonima",
                chain_name="La Anónima",
                price="5350.00",
                promo_type="25%_off",
                captured_at=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
            ),
            ProductSearchOut(
                id=2,
                name="Aceite Mezcla Siglo de Oro x 900 cc.",
                normalized_name="aceite mezcla siglo de oro 900cc",
                brand="Siglo de Oro",
                category="Aceites",
                category_slug="aceites",
                size_quantity=0.9,
                size_unit="cc",
                chain="la_anonima",
                chain_name="La Anónima",
                price="5990.00",
                promo_type=None,
                captured_at=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
            ),
        ]

    async def search_products(
        self,
        query: str,
        chain_slug: str | None,
        limit: int = 20,
        offset: int = 0,
    ) -> ProductSearchPageOut:
        if chain_slug and chain_slug != "la_anonima":
            matches: list[ProductSearchOut] = []
        else:
            matches = [p for p in self.products if query.lower() in p.name.lower()]
        return ProductSearchPageOut(
            products=matches[offset : offset + limit], total=len(matches)
        )

    async def price_history(
        self, product_id: int, span: str | None, chain_slug: str | None = None
    ) -> list[PricePointOut]:
        known = {"la_anonima": 1, "disco": 2}
        if chain_slug is not None and chain_slug not in known:
            return []
        chains = [chain_slug] if chain_slug else ["la_anonima", "disco"]
        return [
            PricePointOut(
                id=known[chain],
                product_id=product_id,
                chain=chain,
                chain_id=known[chain],
                store_id=None,
                price="5350.00",
                promo_type=None,
                captured_at=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
                scraped_at=datetime(2026, 9, 23, 12, 5, tzinfo=UTC),
                hash=chain.ljust(64, "0"),
            )
            for chain in chains
        ]

    async def chains(self) -> list[ChainOut]:
        return [
            ChainOut(id=1, slug="la_anonima", name="La Anónima", active=True),
            ChainOut(id=2, slug="disco", name="Disco", active=True),
        ]

    async def product_detail(self, product_id: int) -> object | None:
        base = next((p for p in self.products if p.id == product_id), None)
        if base is None:
            return None
        point = await self.price_history(product_id, "30d")
        return {
            "id": base.id,
            "name": base.name,
            "normalized_name": base.normalized_name,
            "brand": base.brand,
            "category": base.category,
            "category_slug": base.category_slug,
            "size_quantity": base.size_quantity,
            "size_unit": base.size_unit,
            "ean": base.ean,
            "prices": point,
        }

    async def chain_id(self, slug: str) -> int | None:
        return 1 if slug == "la_anonima" else None


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    """Aísla los contadores de slowapi entre tests."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client() -> httpx.AsyncClient:
    state = AppState(Settings(app_environment="test", database_url="", redis_url=""))
    state.repository = FakeRepository()
    app.state.app_state = state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "scraper"


@pytest.mark.asyncio
async def test_ready_without_dependencies_is_503(client: httpx.AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["title"] == "No listo"
    # Fase 1: Redis es opcional, solo se chequea si está configurado.
    assert body["checks"] == {"database": False}


@pytest.mark.asyncio
async def test_ready_includes_redis_only_when_configured() -> None:
    state = AppState(
        Settings(app_environment="test", database_url="", redis_url="redis://127.0.0.1:1")
    )
    app.state.app_state = state
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": False, "redis": False}


@pytest.mark.asyncio
async def test_search_products_contract(client: httpx.AsyncClient) -> None:
    response = await client.get("/products", params={"q": "aceite"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert len(payload["products"]) == 2
    first = payload["products"][0]
    assert first["id"] == 1
    assert first["name"] == "Aceite de Girasol La Anónima x 900 cc."
    assert first["size_unit"] == "cc"
    assert first["chain"] == "la_anonima"
    assert first["chain_name"] == "La Anónima"
    assert first["price"] == "5350.00"
    assert first["promo_type"] == "25%_off"
    assert first["captured_at"]


@pytest.mark.asyncio
async def test_search_paginates_with_limit_and_offset(client: httpx.AsyncClient) -> None:
    """El sobre `{products,total}` permite paginar sin perder coincidencias."""
    response = await client.get(
        "/products", params={"q": "aceite", "limit": 1, "offset": 1}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert [p["id"] for p in payload["products"]] == [2]


@pytest.mark.asyncio
async def test_search_rejects_limit_above_50(client: httpx.AsyncClient) -> None:
    response = await client.get("/products", params={"q": "aceite", "limit": 51})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_requires_min_length(client: httpx.AsyncClient) -> None:
    response = await client.get("/products", params={"q": "a"})
    assert response.status_code == 422
    assert response.json()["status"] == 422


@pytest.mark.asyncio
async def test_search_filters_by_chain(client: httpx.AsyncClient) -> None:
    response = await client.get("/products", params={"q": "aceite", "chain": "changomas"})
    assert response.status_code == 200
    assert response.json() == {"products": [], "total": 0}


@pytest.mark.asyncio
async def test_price_history_contract(client: httpx.AsyncClient) -> None:
    response = await client.get("/products/1/history", params={"span": "30d"})
    assert response.status_code == 200
    payload = response.json()
    assert [p["chain"] for p in payload] == ["la_anonima", "disco"]
    point = payload[0]
    assert point["price"] == "5350.00"
    assert point["hash"]


@pytest.mark.asyncio
async def test_price_history_filters_by_chain(client: httpx.AsyncClient) -> None:
    response = await client.get("/products/1/history", params={"span": "30d", "chain": "disco"})
    assert response.status_code == 200
    assert [p["chain"] for p in response.json()] == ["disco"]


@pytest.mark.asyncio
async def test_price_history_unknown_chain_is_empty(client: httpx.AsyncClient) -> None:
    response = await client.get("/products/1/history", params={"chain": "changomas"})
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_chains_contract(client: httpx.AsyncClient) -> None:
    response = await client.get("/chains")
    assert response.status_code == 200
    assert response.json() == [
        {"id": 1, "slug": "la_anonima", "name": "La Anónima", "active": True},
        {"id": 2, "slug": "disco", "name": "Disco", "active": True},
    ]


@pytest.mark.asyncio
async def test_product_detail_contract(client: httpx.AsyncClient) -> None:
    response = await client.get("/products/1")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Aceite de Girasol La Anónima x 900 cc."
    assert body["prices"][0]["chain"] == "la_anonima"
    assert body["prices"][0]["price"] == "5350.00"


@pytest.mark.asyncio
async def test_product_detail_not_found_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/products/999")
    assert response.status_code == 404
    body = response.json()
    assert body["title"] == "No encontrado"
    assert body["status"] == 404


@pytest.mark.asyncio
async def test_history_invalid_span_is_422(client: httpx.AsyncClient) -> None:
    response = await client.get("/products/1/history", params={"span": "1y"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_scrape_run_requires_admin_token(client: httpx.AsyncClient) -> None:
    response = await client.post("/scrape/run/la_anonima")
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_rate_limit_get_returns_429(client: httpx.AsyncClient) -> None:
    limit = Settings().rate_limit_requests_per_minute + 1
    for _ in range(limit):
        response = await client.get("/products", params={"q": "aceite"})
    assert response.status_code == 429
    body = response.json()
    assert body["status"] == 429
    assert body["title"] == "Demasiadas peticiones"


# ---------------------------------------------------------------------------
# Rate limit de scrape: fail-closed + buckets por IP (hardening 2026-09-24,
# docs/security.md §3). Una request NO autenticada devuelve 501 sin consumir
# ningún bucket: así un atacante no puede agotar el cupo del admin.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_run_unauth_never_consumes_bucket(
    client: httpx.AsyncClient,
) -> None:
    """H3 (regresión): peticiones sin admin_token NO gastan los buckets de
    rate limit de scrape. Repetimos más allá del límite por usuario: nunca
    debe aparecer un 429 (son todas 501 fall-closed)."""
    limit = Settings().scrape_limit_per_user_hour + 5
    statuses = set()
    for _ in range(limit):
        response = await client.post("/scrape/run/la_anonima")
        statuses.add(response.status_code)
    assert statuses == {501}


@pytest.mark.asyncio
async def test_scrape_run_per_chain_and_user_bucket_are_ip_based() -> None:
    """Los buckets se amaran a la IP, no al header X-Admin-Token (controlable
    por el atacante). Verificamos las claves derivadas en ratelimit.

    Fase 1 no autentica el bucket por cadena sin ejecutar un scrape real, así
    que validamos a nivel de funciones: la clave de cadena incluye la IP y la
    cadena, y nunca el token.
    """
    from app.api import ratelimit as rl
    from starlette.requests import Request

    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": "/scrape/run/la_anonima",
        "path_params": {"chain": "la_anonima"},
        "headers": [(b"x-forwarded-for", b"203.0.113.9")],
        "client": ("203.0.113.9", 53211),
    }
    request = Request(scope)  # type: ignore[arg-type]
    key = rl.chain_key(request)
    assert key == "203.0.113.9:la_anonima"
    assert "admin" not in key
    assert rl.client_ip(request) == "203.0.113.9"
    # Sin X-Forwarded-For se cae a la IP del socket (nunca al token).
    direct = Request({**scope, "headers": [], "client": ("198.51.100.7", 1234)})
    assert rl.client_ip(direct) == "198.51.100.7"


def test_client_ip_ignora_x_forwarded_for_forjado() -> None:
    """Regresión P0.2: un XFF forjado no puede abrir un bucket de rate limit.

    La clave se armaba con el primer valor del header ``X-Forwarded-For``, así
    que un cliente podía mandar ``X-Forwarded-For: <IP-cualquiera>`` y generar
    un bucket nuevo por cada request: el límite de 30/min no limitaba nada.
    A través de nginx no se disparaba (nginx sobreescribe la cabecera), pero el
    puerto 8000 también está publicado en 127.0.0.1 y cualquier cliente que
    llegue sin pasar por el proxy escribía su propio bucket.

    Ahora la IP es la que resolvió el servidor, que solo honra el XFF si el
    peer está en ``--forwarded-allow-ips`` (nginx). Este test simula el peer no
    confiable: la IP del socket manda y el header se ignora.
    """
    from app.api import ratelimit as rl
    from starlette.requests import Request

    forged = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/products",
            "path_params": {},
            "headers": [(b"x-forwarded-for", b"203.0.113.9")],
            "client": ("198.51.100.7", 44444),
        }
    )  # type: ignore[arg-type]
    assert rl.client_ip(forged) == "198.51.100.7"
    # Y una cadena de valores, tampoco: da igual cuántos se inventen.
    chain_forged = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/products",
            "path_params": {},
            "headers": [(b"x-forwarded-for", b"203.0.113.9, 10.0.0.1, 172.16.0.1")],
            "client": ("198.51.100.7", 44445),
        }
    )  # type: ignore[arg-type]
    assert rl.client_ip(chain_forged) == "198.51.100.7"


def test_ratelimit_storage_uri_never_calls_undefined() -> None:
    """Regresión: el storage URI se construía invocando get_redis_password()
    ANTES de su definición (NameError al importar con REDIS_URL configurado)."""
    from app.api import ratelimit as rl

    uri = rl._build_storage_uri()
    assert uri.startswith(("memory://", "redis://", "rediss://"))


def test_ratelimit_storage_uri_injects_password() -> None:
    """REDIS_PASSWORD se inyecta en la URI sin duplicar credenciales."""
    from app.api import ratelimit as rl

    original_url = rl.settings.redis_url
    original_password = rl.settings.redis_password
    try:
        rl.settings.redis_url = "redis://redis:6379"
        rl.settings.redis_password = "p@ss/word"
        assert rl._build_storage_uri() == "redis://:p%40ss%2Fword@redis:6379/3"

        rl.settings.redis_password = ""
        assert rl._build_storage_uri() == "redis://redis:6379/3"

        rl.settings.redis_url = "redis://user:secret@redis:6379/0"
        assert rl._build_storage_uri() == "redis://user:secret@redis:6379/3"

        rl.settings.redis_url = ""
        assert rl._build_storage_uri() == "memory://"
    finally:
        rl.settings.redis_url = original_url
        rl.settings.redis_password = original_password