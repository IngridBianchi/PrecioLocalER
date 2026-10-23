"""Smoke E2E local: scrape real de las cadenas + endpoints contra la DB local.

Uso:
    python scripts/smoke_e2e.py [--chains la_anonima,coto_digital,carrefour]
    DATABASE_URL=... ADMIN_TOKEN=dev python scripts/smoke_e2e.py

Comparte la misma configuración que el servicio (app.main) y no necesita servidor.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "scraper"))

from app.config import Settings  # noqa: E402
from app.main import app  # noqa: E402
from app.state import AppState  # noqa: E402

DEFAULT_CHAINS = ["la_anonima", "coto_digital", "carrefour"]
DEFAULT_TOKEN = "dev"


async def run(chains: list[str], admin_token: str) -> None:
    settings = Settings(
        database_url=os.environ.get(
            "DATABASE_URL",
            "postgresql://preciolocal:preciolocal_dev@localhost:5432/preciolocal",
        ),
        admin_token=admin_token,
        app_environment="test",
    )
    state = AppState(settings)
    app.state.app_state = state

    await state.startup()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        preview = await client.get("/ready")
        print(f"[1] /ready → {preview.status_code} {preview.json()}")

        for chain in chains:
            resp = await client.post(
                f"/scrape/run/{chain}", headers={"X-Admin-Token": admin_token}
            )
            print(f"[2] POST /scrape/run/{chain} → {resp.status_code}")
            if resp.status_code != 200:
                print("    ", resp.text)
                return
            print("    ", resp.json())

        search = await client.get("/products", params={"q": "aceite"})
        print(f"[3] GET /products?q=aceite → {search.status_code} ({len(search.json())} resultados)")
        products = search.json()
        for p in products[:5]:
            print(
                f"     #{p['id']} {p['name']!r:.60} | {p['price']} | "
                f"{p['chain_name']} | {p['captured_at'][:10]} | promo={p['promo_type']}"
            )
        if not products:
            print("    SIN datos — revisar el scrape/parser.")
            return

        pid = products[0]["id"]
        detail = await client.get(f"/products/{pid}")
        print(f"[4] GET /products/{pid} → {detail.status_code}")
        body = detail.json()
        print(f"     {body['name'][:60]!r} — precios {len(body['prices'])}")
        for price in body["prices"]:
            print(f"     {price['chain']} {price['price']} promo={price['promo_type']}")

        history = await client.get(f"/products/{pid}/history", params={"span": "30d"})
        print(f"[5] GET /products/{pid}/history → {history.status_code} ({len(history.json())} pts)")

    await state.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke E2E multi-cadena de PrecioLocal ER")
    parser.add_argument(
        "--chains", default=",".join(DEFAULT_CHAINS), help="slugs separados por coma"
    )
    parser.add_argument(
        "--admin-token", default=os.environ.get("ADMIN_TOKEN", DEFAULT_TOKEN)
    )
    args = parser.parse_args()
    chains = [c.strip() for c in args.chains.split(",") if c.strip()]
    asyncio.run(run(chains, args.admin_token))


if __name__ == "__main__":
    main()