"""Generador de histórico DEMO para poder demostrar el ML sin esperar 14 días.

Por qué existe
--------------
Los modelos necesitan serie: SARIMAX pide >= 14 puntos diarios, Holt-Winters
>= 4 y SsaForecaster (.NET) >= 21. Con el scraping real de un solo día, la
predicción devuelve 422 y el chart del front queda vacío. Este script crea
histórico sintético **para demostrar el pipeline**, nunca para publicar precios.

Guardas duras (por esto el B1 no se repite)
-------------------------------------------
1. Todos los productos se llaman ``DEMO-<slug> ...``: se distinguen de un
   vistazo en la UI y en cualquier export.
2. El nombre normalizado arranca con ``demo-`` y el job de entrenamiento puede
   excluirlos con ``--exclude-demo`` (está en el README de este script).
3. No corre contra producción: aborta si ``APP_ENVIRONMENT=production`` o si
   la URL de la base no es de localhost/127.0.0.1, salvo ``--allow-remote``.
4. Imprime un banner de advertencia con el detalle de lo que insertó.
5. Es reversible: ``--purge`` borra exactamente lo que este script crea
   (productos ``DEMO-`` y sus price_points), y nada más.

La dinámica simulada
--------------------
Inflación mensual + estacionalidad semanal (finde más caro) + promos
esporádicas + ruido. Es lo que hace que SARIMAX con componente estacional y
Holt-Winters tengan algo real que aprender, en vez de ruido blanco.

Uso
---
    python scripts/seed_demo_history.py                  # 60 días, 3 cadenas
    python scripts/seed_demo_history.py --days 90 --dry-run
    python scripts/seed_demo_history.py --purge          # borrar lo generado
    python scripts/seed_demo_history.py --count 6        # más productos

Requiere ``DATABASE_URL`` en el entorno.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

SCRAPER_DIR = Path(__file__).resolve().parent.parent / "services" / "scraper"
sys.path.insert(0, str(SCRAPER_DIR))

from app.config import Settings  # noqa: E402
from app.models import PricePointIn, ProductIn, SkuIn  # noqa: E402
from app.pipelines.dedupe import compute_price_hash  # noqa: E402
from app.pipelines.normalize import extract_size, normalize_name  # noqa: E402
from app.storage.postgres import PoolProvider, ProductRepository  # noqa: E402

DEMO_PREFIX = "DEMO-"
DEMO_SLUGS = [
    "Aceite Girasol 900cc",
    "Harina de Trigo 750gr",
    "Arroz Largo Fino 1kg",
    "Azucar 1kg",
    "Lecha Entera 1L",
    "Fideos Espagueti 500g",
]
DEFAULT_CHAINS = ["la_anonima", "coto_digital", "carrefour"]

# Inflación mensual simulada (la real de 2025-2026 rondó 2-3%).
MONTHLY_INFLATION = 0.025
# Quanto más caro el fin de semana (más demanda, reposición más cara).
WEEKEND_PREMIUM = {5: 0.03, 6: 0.035}
PROMO_EVERY_DAYS = 21
PROMO_DEPTH = Decimal("0.15")


def _price_for_day(base: Decimal, day_index: int, rng: random.Random) -> Decimal:
    """Precio de un producto en un día dado del histórico."""
    drift = (1 + MONTHLY_INFLATION) ** (day_index / 30)
    weekday = datetime(2026, 1, 5) + timedelta(days=day_index)
    seasonal = 1 + WEEKEND_PREMIUM.get(weekday.weekday(), 0.0)
    noise = 1 + rng.uniform(-0.012, 0.012)
    price = base * Decimal(str(drift * seasonal * noise))
    if day_index > 0 and day_index % PROMO_EVERY_DAYS == 0:
        price *= 1 - PROMO_DEPTH
    return price.quantize(Decimal("0.01"))


def _guard(database_url: str, allow_remote: bool) -> None:
    if Settings().app_environment == "production":
        raise SystemExit(
            "ABORT: APP_ENVIRONMENT=production. Este script inserta datos "
            "sintéticos; no puede correr contra la base real."
        )
    host = database_url.split("@")[-1].split("/")[0].split(":")[0]
    if host not in ("localhost", "127.0.0.1") and not allow_remote:
        raise SystemExit(
            f"ABORT: la base no es local (host={host!r}). Si es intencional, "
            "pasá --allow-remote."
        )


async def purge(repo: ProductRepository) -> int:
    """Borra solo lo que este script crea: productos con nombre DEMO-*."""
    pool = repo._provider.pool  # noqa: SLF001 - script de operación, no API
    rows = await pool.fetch(
        "SELECT id FROM products WHERE name LIKE $1 OR normalized_name LIKE $2",
        f"{DEMO_PREFIX}%",
        "demo-%",
    )
    ids = [r["id"] for r in rows]
    if not ids:
        print("[purge] no hay productos DEMO-")
        return 0
    async with pool.acquire() as conn, conn.transaction():
        for pid in ids:
            await conn.execute("DELETE FROM forecasts WHERE product_id = $1", pid)
            await conn.execute("DELETE FROM ml_models WHERE product_id = $1", pid)
            await conn.execute("DELETE FROM anomalies WHERE product_id = $1", pid)
            await conn.execute("DELETE FROM price_points WHERE product_id = $1", pid)
            await conn.execute("DELETE FROM product_skus WHERE product_id = $1", pid)
            await conn.execute("DELETE FROM products WHERE id = $1", pid)
    print(f"[purge] {len(ids)} producto(s) DEMO- eliminados (y sus price_points)")
    return len(ids)


async def seed(
    repo: ProductRepository, chains: list[str], days: int, count: int, dry_run: bool
) -> int:
    chain_ids: dict[str, int] = {}
    for slug in chains:
        cid = await repo.chain_id(slug)
        if cid is None:
            print(f"[seed] AVISO: la cadena '{slug}' no está en la DB; se omite")
            continue
        chain_ids[slug] = cid
    if not chain_ids:
        print("[seed] ninguna cadena válida; nada que hacer")
        return 0

    total = 0
    today = datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=0)
    for index, raw_title in enumerate(DEMO_SLUGS[:count]):
        title = f"{DEMO_PREFIX}{raw_title}"
        normalized = normalize_name(title)
        size = extract_size(title)
        rng = random.Random(1000 + index)
        base = Decimal(str(round(rng.uniform(3200, 9800), 2)))

        for slug, chain_id in chain_ids.items():
            # Cada cadena tiene su propio precio de partida: comparar cadenas
            # es justamente lo que hace la app.
            chain_base = (base * Decimal(str(rng.uniform(0.88, 1.12)))).quantize(
                Decimal("0.01")
            )
            product_id = None
            sku_id = None
            if dry_run:
                product_id, sku_id = 0, 0
            else:
                product_id = await repo.upsert_product(
                    ProductIn(
                        name=title,
                        normalized_name=normalized,
                        size_quantity=size.quantity,
                        size_unit=size.unit,
                    )
                )
                if product_id is None:
                    continue
                external_id = f"demo-{slug}-{index:03d}"
                sku_id = await repo.upsert_sku(
                    SkuIn(
                        product_id=product_id,
                        chain_id=chain_id,
                        external_id=external_id,
                        name=title,
                    )
                )
                if sku_id is None:
                    continue

            for day_index in range(days):
                captured_at = today - timedelta(days=days - 1 - day_index)
                captured_at += timedelta(hours=day_index % 6)
                price = _price_for_day(chain_base, day_index, rng)
                promo = (
                    "15%_off"
                    if day_index > 0 and day_index % PROMO_EVERY_DAYS == 0
                    else None
                )
                if dry_run:
                    total += 1
                    continue
                inserted, _ = await repo.insert_price_point(
                    PricePointIn(
                        product_id=product_id,
                        chain_id=chain_id,
                        sku_id=sku_id,
                        store_id=None,
                        price=price,
                        promo_type=promo,
                        captured_at=captured_at,
                        raw_json={"source": "seed_demo_history", "chain": slug},
                        hash=compute_price_hash(
                            chain_id, sku_id, str(price), captured_at.isoformat()
                        ),
                    )
                )
                total += int(inserted)
        print(f"  {title} ({len(chain_ids)} cadenas x {days} días)")

    if not dry_run:
        print(f"[seed] {total} price_points DEMO- insertados")
    else:
        print(f"[seed] dry-run: habrí insertado {total} price_points")
    return total


async def run() -> int:
    parser = argparse.ArgumentParser(description="Histórico sintético para demo de ML")
    parser.add_argument("--days", type=int, default=60, help="días de histórico (default 60)")
    parser.add_argument("--count", type=int, default=4, help="cuántos productos DEMO-")
    parser.add_argument(
        "--chains",
        default=",".join(DEFAULT_CHAINS),
        help="slugs separados por coma",
    )
    parser.add_argument("--purge", action="store_true", help="borra los DEMO- y sale")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="permite una base que no sea localhost (no usar en producción)",
    )
    args = parser.parse_args()

    database_url = (
        Settings().database_url
        or os.environ.get("DATABASE_URL")
        or "postgresql://preciolocal:preciolocal@localhost:5432/preciolocal"
    )
    _guard(database_url, args.allow_remote)

    provider = PoolProvider(database_url)
    await provider.start()
    repo = ProductRepository(provider)
    try:
        if args.purge:
            await purge(repo)
            return 0
        if args.days < 21:
            print(
                f"[seed] AVISO: {args.days} días no alcanzan para SsaForecaster (.NET "
                "pide 21). El forecast de Python va a andar; el de .NET no."
            )
        print(
            f"[seed] {DEMO_PREFIX} histórico SINTÉTICO · {args.days} días · "
            f"cadenas: {args.chains} · NO son precios reales de góndola."
        )
        await seed(repo, args.chains.split(","), args.days, args.count, args.dry_run)
        return 0
    finally:
        await provider.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
