"""Contract tests: la API real contra `docs/contracts.md`.

La doc dice (línea 3) que los contratos se versionan a mano "hasta el primer
release; luego se congelan y se testean con contract tests". Este módulo es esa
congelación, del lado del scraper.

No reemplaza a `test_api.py`: ese verifica comportamiento, este verifica que
la API y el documento no se separen. Un contrato que se rompe sin que nadie se
avise es peor que uno sin documentar, porque el front y el gatewayFranz se
ajustan a algo que ya no existe.

Lo que se fija:

1. Que cada endpoint de la tabla §3.1 exista en la app, y que la app no tenga
   endpoints públicos que la doc no mencione (deriva en ambas direcciones).
2. Que las entidades `Product`, `PricePoint` y `Prediction` tengan exactamente
   las claves del ejemplo JSON de la doc.
3. Las convenciones de §1 que son verificables: precio como **string** decimal,
   `snake_case`, fechas ISO 8601 UTC, errores RFC 7807.
4. Las enumeraciones de §4: `size_unit` y los nombres de modelo.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from app.api.ratelimit import limiter
from app.config import Settings
from app.main import app
from app.ml.series import PriceSeries
from app.state import AppState

CONTRACTS = Path(__file__).resolve().parents[4] / "docs" / "contracts.md"
TEXT = CONTRACTS.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# Utilidades para leer la doc (no hardcodear lo que ya está escrito)
# ---------------------------------------------------------------------------


def _json_block(heading: str) -> dict:
    """Primer bloque ```json``` después de un heading `### X`."""
    tail = TEXT.split(f"### {heading}", 1)[1]
    block = re.search(r"```json\n(.*?)\n```", tail, re.DOTALL)
    assert block, f"no encontré un bloque json bajo '### {heading}'"
    return json.loads(block.group(1))


def _documented_endpoints() -> dict[str, str]:
    """Rutas de la tabla §3.1 del scraper: {path_template: metodo}.

    La tabla documenta la ruta con su query string (`/products?q=&chain=`),
    pero la ruta de FastAPI no incluye los query params: se separan.
    """
    section = TEXT.split("### 3.1 Scraper (Python)", 1)[1].split("### 3.2", 1)[0]
    found: dict[str, str] = {}
    for raw_path, method in re.findall(
        r"^\|\s*`([^`]+)`\s*\|\s*(GET|POST)\s*\|", section, re.MULTILINE
    ):
        found[raw_path.split("?", 1)[0]] = method
    return found


DOCUMENTED = _documented_endpoints()

# Rutas que existen para operación/infraestructura y la doc agrupa aparte.
UNDOCUMENTED_OK = {
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}


def _live_routes() -> dict[str, str]:
    """Rutas públicas reales de la app: {path_template: metodo}.

    Se lee del OpenAPI y no de `app.routes`: la versión de FastAPI instalada
    envuelve los `include_router` en objetos `_IncludedRouter` sin `path`, así
    que recorrer `app.routes` a mano depende de la versión y hoy devuelve solo
    las cuatro rutas de documentación.
    """
    schema = app.openapi()
    routes: dict[str, str] = {}
    for path, operations in schema.get("paths", {}).items():
        verbs = [v.upper() for v in operations if v.upper() in ("GET", "POST", "PUT", "DELETE")]
        if verbs:
            routes[path] = "GET" if "GET" in verbs else sorted(verbs)[0]
    return routes


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    limiter.reset()
    yield
    limiter.reset()


# ---------------------------------------------------------------------------
# 1. La tabla de endpoints y la app no pueden divergir
# ---------------------------------------------------------------------------


def test_contract_doc_lists_the_expected_endpoints() -> None:
    """Guardia del propio test: si el parser de la tabla deja de encontrar
    filas, los tests de abajo pasarían en verde sobre una doc vacía."""
    assert len(DOCUMENTED) >= 10
    assert "/products" in DOCUMENTED
    assert "/ml/forecast/{product_id}" in DOCUMENTED


@pytest.mark.parametrize("path", sorted(DOCUMENTED))
def test_documented_endpoint_exists(path: str) -> None:
    live = _live_routes()
    assert path in live, f"docs/contracts.md documenta {path} y la app no lo expone"


@pytest.mark.parametrize("path", sorted(DOCUMENTED))
def test_documented_method_matches(path: str) -> None:
    assert _live_routes()[path] == DOCUMENTED[path]


def test_app_has_no_undocumented_endpoint() -> None:
    """Deriva inversa: un endpoint nuevo sin documentar rompe al gateway y al
    front sin que nadie se entere hasta producción."""
    extra = {
        path
        for path, verb in _live_routes().items()
        if path not in DOCUMENTED and path not in UNDOCUMENTED_OK and verb != "HEAD"
    }
    assert not extra, f"endpoints sin documentar en §3.1: {sorted(extra)}"


# ---------------------------------------------------------------------------
# 2. Las entidades tienen las claves del ejemplo
# ---------------------------------------------------------------------------


def test_product_entity_matches_the_doc() -> None:
    documented = set(_json_block("Product"))
    # La respuesta de detalle envuelve Product con `prices`.
    assert documented == {
        "id",
        "name",
        "normalized_name",
        "brand",
        "category",
        "category_slug",
        "size_quantity",
        "size_unit",
        "ean",
    }


async def test_product_detail_really_has_the_documented_keys() -> None:
    state = AppState(Settings(app_environment="test", database_url="", redis_url=""))
    state.repository = _Repo()  # type: ignore[assignment]
    app.state.app_state = state
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = (await client.get("/products/1")).json()
    assert set(body) == set(_json_block("Product")) | {"prices"}
    assert set(body["prices"][0]) == set(_json_block("PricePoint"))


async def test_price_point_entity_matches_the_doc() -> None:
    assert set(_json_block("PricePoint")) == {
        "id",
        "product_id",
        "chain",
        "chain_id",
        "sku_id",
        "store_id",
        "price",
        "promo_type",
        "captured_at",
        "scraped_at",
        "hash",
    }


async def test_prediction_entity_matches_the_doc() -> None:
    state = AppState(Settings(app_environment="test", database_url="", redis_url=""))
    state.repository = _Repo()  # type: ignore[assignment]
    app.state.app_state = state
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = (await client.get("/ml/forecast/1")).json()
    assert set(body) == set(_json_block("Prediction"))


async def test_search_response_rows_match_the_product_entity_plus_price() -> None:
    """`GET /products` devuelve una fila por (producto, cadena): Product + precio."""
    state = AppState(Settings(app_environment="test", database_url="", redis_url=""))
    state.repository = _Repo()  # type: ignore[assignment]
    app.state.app_state = state
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = (await client.get("/products", params={"q": "aceite"})).json()
    rows = body["products"]
    assert rows
    extra = {"chain", "chain_name", "price", "promo_type", "captured_at"}
    assert set(rows[0]) == set(_json_block("Product")) | extra
    assert body["total"] == 1


# ---------------------------------------------------------------------------
# 3. Convenciones de §1
# ---------------------------------------------------------------------------


async def test_prices_are_decimal_strings_not_numbers() -> None:
    """§1: en el scraper el precio viaja como string para no perder precisión.

    Es la convención más fácil de romper sin querer (cambiar un `str(...)` por un
    float) y la más cara: `4599.50` pasa a `4599.5` y el front muestra de más en
    los tickets.
    """
    state = AppState(Settings(app_environment="test", database_url="", redis_url=""))
    state.repository = _Repo()  # type: ignore[assignment]
    app.state.app_state = state
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        raw = (await client.get("/products", params={"q": "aceite"})).text
    body = json.loads(raw)
    assert isinstance(body["products"][0]["price"], str), (
        "el precio tiene que ser string en el scraper"
    )
    # Y con el texto crudo se ve que conserva los dos decimales.
    assert '"4599.50"' in raw
    Decimal(body["products"][0]["price"])


async def test_field_names_are_snake_case() -> None:
    """§1: el scraper serializa en snake_case (a diferencia de .NET)."""
    state = AppState(Settings(app_environment="test", database_url="", redis_url=""))
    state.repository = _Repo()  # type: ignore[assignment]
    app.state.app_state = state
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = (await client.get("/products/1")).json()
    camel = [k for k in body if re.search(r"[a-z][A-Z]", k)]
    assert not camel, f"claves en camelCase en el scraper: {camel}"


async def test_timestamps_are_iso8601_utc() -> None:
    state = AppState(Settings(app_environment="test", database_url="", redis_url=""))
    state.repository = _Repo()  # type: ignore[assignment]
    app.state.app_state = state
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        point = (await client.get("/products/1")).json()["prices"][0]
    for key in ("captured_at", "scraped_at"):
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", point[key]), point[key]
        assert point[key].endswith("Z") or point[key].endswith("+00:00"), point[key]


async def test_errors_are_rfc7807_problem_json() -> None:
    """§1: `title`, `status`, `detail`, `instance`."""
    state = AppState(Settings(app_environment="test", database_url="", redis_url=""))
    state.repository = _Repo()  # type: ignore[assignment]
    app.state.app_state = state
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/products/999")
    assert response.status_code == 404
    body = response.json()
    assert {"title", "status", "detail"} <= set(body)
    assert body["status"] == 404


# ---------------------------------------------------------------------------
# 4. Enumeraciones de §4
# ---------------------------------------------------------------------------


def test_size_unit_enumeration_matches_the_parser() -> None:
    """§4: `kg` · `L` · `g` · `cc` · `und` · `pack`.

    Se cruza la doc contra lo que `_CANONICAL_UNIT` puede emitir de verdad: si
    el parser agrega una unidad nueva y nadie actualiza la doc, el front no
    sabe cómo mostrarla.
    """
    from app.pipelines.normalize import _CANONICAL_UNIT

    documented = {"kg", "L", "g", "cc", "und", "pack"}
    emitted = set(_CANONICAL_UNIT.values())
    undocumented = emitted - documented
    assert not undocumented, f"unidades que el parser emite y la doc no lista: {undocumented}"


def test_model_names_are_the_documented_ones() -> None:
    """§2: los nombres de modelo Python son `sarimax` y `holtwinters`."""
    from app.ml.models import MODEL_HOLT_WINTERS, MODEL_SARIMAX

    assert MODEL_SARIMAX == "sarimax"
    assert MODEL_HOLT_WINTERS == "holtwinters"
    assert '"model": "holtwinters"' in TEXT


def test_chain_slugs_are_the_documented_five() -> None:
    """§4: cinco cadenas; tres con scraper, dos solo seedeadas."""
    section = TEXT.split("### chain", 1)[1].split("### size_unit", 1)[0]
    slugs = re.findall(r"^\|\s*`([a-z_]+)`\s*\|", section, re.MULTILINE)
    assert slugs == ["la_anonima", "coto_digital", "carrefour", "cooperativa_obrera", "changomas"]


def test_chains_seeded_in_the_database_match_the_doc() -> None:
    """Lo que la doc llama "datos reales a 2026-09-25" tiene que seguir siendo
    lo que las migraciones insertan.

    No alcanza con mirar `002_seed.sql`: las tres primeras cadenas están ahí y
    `coto_digital` / `carrefour` llegaron con `005_add_coto_carrefour.sql`, así
    que la verificación tiene que ser sobre el conjunto de todas las
    migraciones.
    """
    migrations = CONTRACTS.parents[1] / "db" / "supabase" / "migrations"
    sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted(migrations.glob("*.sql")))
    for slug in ("la_anonima", "coto_digital", "carrefour", "cooperativa_obrera", "changomas"):
        assert f"'{slug}'" in sql, f"{slug} está en la doc pero ninguna migración lo siembra"


# ---------------------------------------------------------------------------
# Fake mínimo: los endpoints de contrato necesitan datos
# ---------------------------------------------------------------------------


def _series(days: int = 30) -> PriceSeries:
    from datetime import date, timedelta

    today = date(2026, 10, 1)
    return PriceSeries(
        days=[today - timedelta(days=days - 1 - i) for i in range(days)],
        values=[1000.0 + 9 * i for i in range(days)],
    )


class _Repo:
    async def search_products(
        self,
        query: str,
        chain_slug: str | None,
        limit: int = 20,
        offset: int = 0,
    ):
        from datetime import UTC, datetime

        from app.models import ProductSearchOut, ProductSearchPageOut

        rows = [
            ProductSearchOut(
                id=1,
                name="Aceite Girasol 900 cc",
                normalized_name="aceite girasol 900cc",
                brand="Natura",
                category="Aceites",
                category_slug="aceites",
                size_quantity=Decimal("0.9"),
                size_unit="cc",
                ean="7790070000000",
                chain="la_anonima",
                chain_name="La Anónima",
                price="4599.50",
                promo_type=None,
                captured_at=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            )
        ][offset : offset + limit]
        return ProductSearchPageOut(products=rows, total=1)

    async def product_detail(self, product_id: int) -> dict[str, object] | None:
        from datetime import UTC, datetime

        from app.models import PricePointOut, ProductOut

        if product_id != 1:
            return None
        point = PricePointOut(
            id=55678,
            product_id=1,
            chain="la_anonima",
            chain_id=1,
            sku_id=42,
            store_id=7,
            price="4599.50",
            promo_type=None,
            captured_at=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            scraped_at=datetime(2026, 3, 1, 12, 5, tzinfo=UTC),
            hash="a" * 64,
        )
        product = ProductOut(
            id=1,
            name="Aceite Girasol 900 cc",
            normalized_name="aceite girasol 900cc",
            brand="Natura",
            category="Aceites",
            category_slug="aceites",
            size_quantity=Decimal("0.9"),
            size_unit="cc",
            ean="7790070000000",
        )
        return {**product.model_dump(mode="json"), "prices": [point.model_dump(mode="json")]}

    async def price_series_for_forecast(self, product_id: int, chain: str | None) -> list:
        from datetime import date, timedelta
        from decimal import Decimal as D

        today = date(2026, 10, 1)
        return [
            (today - timedelta(days=29 - i), D(str(1000.0 + 9 * i))) for i in range(30)
        ]

    async def chains(self) -> list:  # pragma: no cover - no usado por estos tests
        return []

    async def price_history(self, *args: object, **kwargs: object) -> list:  # pragma: no cover
        return []
