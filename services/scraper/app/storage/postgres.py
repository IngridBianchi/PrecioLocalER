"""Acceso a PostgreSQL (asyncpg) + Redis para readiness.

El import de los drivers es perezoso adentro de los métodos para que los
tests unitarios/integración con repositorios fake no requieran las libs nativas.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.models import (
    CategoryPriceOut,
    ChainOut,
    PricePointIn,
    PricePointOut,
    ProductDetailOut,
    ProductIn,
    ProductSearchOut,
    ProductSearchPageOut,
    SkuIn,
)

SPANS = {"7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90)}


class PoolProvider:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any | None = None

    async def start(self) -> None:
        import asyncpg  # lazy: evita requerir la lib en tests sin DB

        self._pool = await asyncpg.create_pool(dsn=self._database_url, min_size=1, max_size=5)

    async def stop(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> Any:
        if self._pool is None:
            raise RuntimeError("pool de postgres no iniciado")
        return self._pool


class ProductRepository:
    def __init__(self, pool_provider: PoolProvider) -> None:
        self._provider = pool_provider

    async def chain_id(self, slug: str) -> int | None:
        row = await self._provider.pool.fetchrow(
            "SELECT id FROM chains WHERE slug = $1 AND active = true", slug
        )
        return row["id"] if row else None

    async def category_id_by_path(self, path: str | None) -> int | None:
        if not path:
            return None
        segment = path.replace(">", "/").split("/")[-1].strip()
        if not segment:
            return None
        slug = segment.lower().replace(" ", "-")
        value = await self._provider.pool.fetchval(
            "SELECT id FROM categories WHERE slug = $1 OR name ILIKE $2 LIMIT 1",
            slug,
            segment,
        )
        return int(value) if value is not None else None

    async def category_id_by_slug(self, slug: str | None) -> int | None:
        if not slug:
            return None
        value = await self._provider.pool.fetchval(
            "SELECT id FROM categories WHERE slug = $1 LIMIT 1", slug
        )
        return int(value) if value is not None else None

    async def chains(self) -> list[ChainOut]:
        """Cadenas activas. Es la fuente del selector de la web: la lista no
        está hardcodeada en el frontend, así que agregar una cadena en la DB
        la hace aparecer sin tocar el bundle."""
        rows = await self._provider.pool.fetch(
            "SELECT id, slug, name, active FROM chains WHERE active = true ORDER BY name"
        )
        return [
            ChainOut(id=r["id"], slug=r["slug"], name=r["name"], active=r["active"])
            for r in rows
        ]

    async def upsert_product(self, product: ProductIn) -> int | None:
        """Inserta o actualiza por normalized_name; devuelve el id.

        El DO UPDATE refresca toda la presentación, `size_unit` incluida: si no,
        la fila puede quedar con la unidad del primer scrape y la cantidad del
        último. Por el pipeline actual `size_unit` es función de
        `normalized_name`, así que el síntoma no se llega a ver, pero dejar una
        columna fuera del UPDATE es una inconsistencia latente (detectada al
        agregar los integration tests el 2026-10-01).
        """
        row = await self._provider.pool.fetchrow(
            """
            INSERT INTO products (name, normalized_name, size_quantity, size_unit, category_id, created_at)
            VALUES ($1, $2, $3, $4, $5, now())
            ON CONFLICT (normalized_name)
            DO UPDATE SET name = EXCLUDED.name,
                          size_quantity = EXCLUDED.size_quantity,
                          size_unit = EXCLUDED.size_unit,
                          category_id = COALESCE(EXCLUDED.category_id, products.category_id)
            RETURNING id
            """,
            product.name,
            product.normalized_name,
            None if product.size_quantity is None else str(product.size_quantity),
            product.size_unit,
            product.category_id,
        )
        return row["id"] if row else None

    async def upsert_sku(self, sku: SkuIn) -> int | None:
        """Inserta o actualiza el SKU de venta real de una cadena.

        Un mismo producto canónico puede tener varios SKU por cadena
        (Coto 531487/531488); (chain_id, external_id) es la identidad.
        """
        row = await self._provider.pool.fetchrow(
            """
            INSERT INTO product_skus
                (product_id, chain_id, external_id, name, url, active, first_seen_at, last_seen_at)
            VALUES ($1, $2, $3, $4, $5, true, now(), now())
            ON CONFLICT (chain_id, external_id)
            DO UPDATE SET name = EXCLUDED.name, url = EXCLUDED.url, active = true,
                          last_seen_at = now()
            RETURNING id
            """,
            sku.product_id,
            sku.chain_id,
            sku.external_id,
            sku.name,
            sku.url,
        )
        return row["id"] if row else None

    async def deactivate_missing_skus(self, chain_id: int, seen_after: datetime) -> int:
        """Da de baja los SKU de una cadena que no aparecieron en el scrape actual.

        Por qué: `upsert_sku` marca `active = true` y refresca `last_seen_at`
        en cada sighting. Un SKU que la cadena saca de góndela no vuelve a
        aparecer, así que sin este barrido seguiría figurando como precio
        vigente para siempre (deuda T8). `search_products` y `product_detail`
        ya filtran por `s.active`, así que apagarlos los saca del resultado
        sin borrar histórico: `price_history` no mira `active` a propósito.

        `seen_after` es el instante en que arrancó el scrape; cualquier SKU
        con `last_seen_at` anterior no fue visto en esta pasada.
        """
        result = await self._provider.pool.execute(
            """
            UPDATE product_skus
            SET active = false
            WHERE chain_id = $1 AND active AND last_seen_at < $2
            """,
            chain_id,
            seen_after,
        )
        # asyncpg devuelve "UPDATE <n>"
        return int(result.rsplit(" ", 1)[-1])

    async def insert_price_point(self, point: PricePointIn) -> tuple[bool, int]:
        """Inserta idempotente por hash; devuelve (insertado, id)."""
        row = await self._provider.pool.fetchrow(
            """
            INSERT INTO price_points
                (product_id, store_id, chain_id, sku_id, price, promo_type, captured_at,
                 raw_json, hash)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (hash) DO NOTHING
            RETURNING id
            """,
            point.product_id,
            point.store_id,
            point.chain_id,
            point.sku_id,
            str(point.price),
            point.promo_type,
            point.captured_at,
            json.dumps(point.raw_json, default=str),
            point.hash,
        )
        if row:
            return True, row["id"]
        return False, 0

    async def search_products(
        self, query: str, chain_slug: str | None, limit: int = 20, offset: int = 0
    ) -> ProductSearchPageOut:
        """Búsqueda ILIKE con el precio representativo por (producto, cadena).

        Regla (incidente 2026-09-24): un producto puede tener varios SKU por
        cadena; el representativo de cada (producto, cadena) es el del SKU
        activo con MENOR precio en su última captura (regla "desde $X").
        Se resuelve con 2 capas de DISTINCT ON:
          1) último price_point por SKU;
          2) mínimo de esos precios por (producto, cadena).
        
        Ordenamiento por relevancia:
        - Text match: exact (100) > normalized exact (90) > partial (50/40) > other (10)
        - Price rank: menor precio por producto = mejor rank
        - Recency: más reciente = mayor score
        - Promo: con promo_type = bonus
        - Active: ya filtrado en latest_per_sku
        """
        query_exact = query
        query_partial = f"%{query}%"
        
        if chain_slug:
            sql = """
                WITH latest_per_sku AS (
                    SELECT DISTINCT ON (pp.sku_id)
                           pp.product_id, pp.chain_id, pp.price, pp.promo_type, pp.captured_at
                    FROM price_points pp
                    JOIN product_skus s ON s.id = pp.sku_id
                    WHERE pp.sku_id IS NOT NULL AND s.active
                    ORDER BY pp.sku_id, pp.captured_at DESC, pp.id DESC
                ),
                rep AS (
                    SELECT DISTINCT ON (l.product_id, l.chain_id)
                           l.product_id, l.chain_id, l.price, l.promo_type, l.captured_at
                    FROM latest_per_sku l
                    ORDER BY l.product_id, l.chain_id, l.price ASC, l.captured_at DESC
                )
                SELECT p.id, p.name, p.normalized_name, p.size_quantity, p.size_unit,
                       b.name AS brand, c.name AS category, c.slug AS category_slug, p.ean,
                       ch.slug AS chain, ch.name AS chain_name,
                       r.price, r.promo_type, r.captured_at,
                       (
                         CASE 
                           WHEN p.name ILIKE $2 THEN 100
                           WHEN p.normalized_name ILIKE $2 THEN 90
                           WHEN p.name ILIKE $3 THEN 50
                           WHEN p.normalized_name ILIKE $3 THEN 40
                           ELSE 10
                         END * 0.5
                         +
                         (50 - LEAST(50, ROW_NUMBER() OVER (
                             PARTITION BY p.id ORDER BY r.price ASC)) * 5) * 0.2
                         +
                         GREATEST(0, 50 - EXTRACT(EPOCH FROM (NOW() - r.captured_at))/86400) * 0.1
                         +
                         CASE WHEN r.promo_type IS NOT NULL THEN 30 ELSE 0 END * 0.15
                         +
                         10 * 0.05
                       ) AS score
                FROM rep r
                JOIN products p ON p.id = r.product_id
                JOIN chains ch ON ch.id = r.chain_id
                LEFT JOIN brands b ON b.id = p.brand_id
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE ch.slug = $1
                  AND (p.name ILIKE $3 OR p.normalized_name ILIKE $3)
                ORDER BY score DESC, p.id, ch.slug
                LIMIT $4 OFFSET $5
            """
            rows = await self._provider.pool.fetch(
                sql, chain_slug, query_exact, query_partial, limit, offset
            )
            
            # Count total for pagination
            count_sql = """
                WITH latest_per_sku AS (
                    SELECT DISTINCT ON (pp.sku_id)
                           pp.product_id, pp.chain_id, pp.price, pp.promo_type, pp.captured_at
                    FROM price_points pp
                    JOIN product_skus s ON s.id = pp.sku_id
                    WHERE pp.sku_id IS NOT NULL AND s.active
                    ORDER BY pp.sku_id, pp.captured_at DESC, pp.id DESC
                ),
                rep AS (
                    SELECT DISTINCT ON (l.product_id, l.chain_id)
                           l.product_id, l.chain_id, l.price, l.promo_type, l.captured_at
                    FROM latest_per_sku l
                    ORDER BY l.product_id, l.chain_id, l.price ASC, l.captured_at DESC
                )
                SELECT COUNT(*)
                FROM rep r
                JOIN products p ON p.id = r.product_id
                JOIN chains ch ON ch.id = r.chain_id
                WHERE ch.slug = $1
                  AND (p.name ILIKE $2 OR p.normalized_name ILIKE $2)
            """
            total = await self._provider.pool.fetchval(count_sql, chain_slug, query_partial)
        else:
            sql = """
                WITH latest_per_sku AS (
                    SELECT DISTINCT ON (pp.sku_id)
                           pp.product_id, pp.chain_id, pp.price, pp.promo_type, pp.captured_at
                    FROM price_points pp
                    JOIN product_skus s ON s.id = pp.sku_id
                    WHERE pp.sku_id IS NOT NULL AND s.active
                    ORDER BY pp.sku_id, pp.captured_at DESC, pp.id DESC
                ),
                rep AS (
                    SELECT DISTINCT ON (l.product_id, l.chain_id)
                           l.product_id, l.chain_id, l.price, l.promo_type, l.captured_at
                    FROM latest_per_sku l
                    ORDER BY l.product_id, l.chain_id, l.price ASC, l.captured_at DESC
                )
                SELECT p.id, p.name, p.normalized_name, p.size_quantity, p.size_unit,
                       b.name AS brand, c.name AS category, c.slug AS category_slug, p.ean,
                       ch.slug AS chain, ch.name AS chain_name,
                       r.price, r.promo_type, r.captured_at,
                       (
                         CASE 
                           WHEN p.name ILIKE $1 THEN 100
                           WHEN p.normalized_name ILIKE $1 THEN 90
                           WHEN p.name ILIKE $2 THEN 50
                           WHEN p.normalized_name ILIKE $2 THEN 40
                           ELSE 10
                         END * 0.5
                         +
                         (50 - LEAST(50, ROW_NUMBER() OVER (
                             PARTITION BY p.id ORDER BY r.price ASC)) * 5) * 0.2
                         +
                         GREATEST(0, 50 - EXTRACT(EPOCH FROM (NOW() - r.captured_at))/86400) * 0.1
                         +
                         CASE WHEN r.promo_type IS NOT NULL THEN 30 ELSE 0 END * 0.15
                         +
                         10 * 0.05
                       ) AS score
                FROM rep r
                JOIN products p ON p.id = r.product_id
                JOIN chains ch ON ch.id = r.chain_id
                LEFT JOIN brands b ON b.id = p.brand_id
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE p.name ILIKE $2 OR p.normalized_name ILIKE $2
                ORDER BY score DESC, p.id, ch.slug
                LIMIT $3 OFFSET $4
            """
            rows = await self._provider.pool.fetch(sql, query_exact, query_partial, limit, offset)
            
            # Count total for pagination
            count_sql = """
                WITH latest_per_sku AS (
                    SELECT DISTINCT ON (pp.sku_id)
                           pp.product_id, pp.chain_id, pp.price, pp.promo_type, pp.captured_at
                    FROM price_points pp
                    JOIN product_skus s ON s.id = pp.sku_id
                    WHERE pp.sku_id IS NOT NULL AND s.active
                    ORDER BY pp.sku_id, pp.captured_at DESC, pp.id DESC
                ),
                rep AS (
                    SELECT DISTINCT ON (l.product_id, l.chain_id)
                           l.product_id, l.chain_id, l.price, l.promo_type, l.captured_at
                    FROM latest_per_sku l
                    ORDER BY l.product_id, l.chain_id, l.price ASC, l.captured_at DESC
                )
                SELECT COUNT(*)
                FROM rep r
                JOIN products p ON p.id = r.product_id
                JOIN chains ch ON ch.id = r.chain_id
                WHERE p.name ILIKE $1 OR p.normalized_name ILIKE $1
            """
            total = await self._provider.pool.fetchval(count_sql, query_partial)
        
        products = [_row_to_search(r) for r in rows]
        return ProductSearchPageOut(products=products, total=total)

    async def product_detail(self, product_id: int) -> ProductDetailOut | None:
        """Metadatos del producto + último price_point por cadena."""
        row = await self._provider.pool.fetchrow(
            """
            SELECT p.id, p.name, p.normalized_name, p.size_quantity, p.size_unit, p.ean,
                   b.name AS brand, c.name AS category, c.slug AS category_slug
            FROM products p
            LEFT JOIN brands b ON b.id = p.brand_id
            LEFT JOIN categories c ON c.id = p.category_id
            WHERE p.id = $1
            """,
            product_id,
        )
        if row is None:
            return None
        prices = await self._provider.pool.fetch(
            """
            WITH latest_per_sku AS (
                SELECT DISTINCT ON (pp.sku_id)
                       pp.id, pp.sku_id, pp.product_id, pp.chain_id, pp.store_id,
                       pp.price, pp.promo_type, pp.captured_at, pp.scraped_at, pp.hash
                FROM price_points pp
                JOIN product_skus s ON s.id = pp.sku_id
                WHERE pp.product_id = $1 AND pp.sku_id IS NOT NULL AND s.active
                ORDER BY pp.sku_id, pp.captured_at DESC, pp.id DESC
            ),
            rep AS (
                SELECT DISTINCT ON (l.chain_id)
                       l.id, l.sku_id, l.product_id, l.chain_id, l.store_id,
                       l.price, l.promo_type, l.captured_at, l.scraped_at, l.hash
                FROM latest_per_sku l
                ORDER BY l.chain_id, l.price ASC, l.captured_at DESC
            )
            SELECT r.id, r.sku_id, r.product_id, ch.slug AS chain, r.chain_id, r.store_id,
                   r.price, r.promo_type, r.captured_at, r.scraped_at, r.hash
            FROM rep r
            JOIN chains ch ON ch.id = r.chain_id
            ORDER BY ch.slug
            """,
            product_id,
        )
        return ProductDetailOut(
            id=row["id"],
            name=row["name"],
            normalized_name=row["normalized_name"],
            brand=row.get("brand"),
            category=row.get("category"),
            category_slug=row.get("category_slug"),
            size_quantity=row.get("size_quantity"),
            size_unit=row.get("size_unit") or "und",
            ean=row.get("ean"),
            prices=[_row_to_point(r) for r in prices],
        )

    async def price_history(
        self, product_id: int, span: str | None, chain_slug: str | None = None
    ) -> list[PricePointOut]:
        days = SPANS.get(span or "30d", SPANS["30d"])
        cutoff = datetime.now(UTC) - days
        rows = await self._provider.pool.fetch(
            """
            SELECT pp.id, pp.product_id, ch.slug AS chain, pp.chain_id, pp.sku_id,
                   pp.store_id, pp.price, pp.promo_type, pp.captured_at, pp.scraped_at, pp.hash
            FROM price_points pp
            JOIN chains ch ON ch.id = pp.chain_id
            WHERE pp.product_id = $1
              AND pp.captured_at >= $2
              AND ($3::text IS NULL OR ch.slug = $3)
            ORDER BY pp.captured_at DESC
            """,
            product_id,
            cutoff,
            chain_slug,
        )
        return [_row_to_point(r) for r in rows]

    async def category_prices(
        self, category_slug: str, chain_slug: str | None = None, limit: int = 50
    ) -> list[CategoryPriceOut]:
        """Último precio vigente por (producto, cadena) dentro de una categoría."""
        rows = await self._provider.pool.fetch(
            """
            WITH latest_per_sku AS (
                SELECT DISTINCT ON (pp.sku_id)
                       pp.id, pp.product_id, pp.chain_id, pp.sku_id, pp.store_id,
                       pp.price, pp.promo_type, pp.captured_at, pp.scraped_at, pp.hash
                FROM price_points pp
                JOIN product_skus s ON s.id = pp.sku_id
                WHERE pp.sku_id IS NOT NULL AND s.active
                ORDER BY pp.sku_id, pp.captured_at DESC, pp.id DESC
            ),
            rep AS (
                SELECT DISTINCT ON (l.product_id, l.chain_id)
                       l.id, l.product_id, l.chain_id, l.sku_id, l.store_id,
                       l.price, l.promo_type, l.captured_at, l.scraped_at, l.hash
                FROM latest_per_sku l
                ORDER BY l.product_id, l.chain_id, l.price ASC, l.captured_at DESC
            )
            SELECT r.id, r.product_id, p.name AS product_name, ch.slug AS chain,
                   r.chain_id, r.price, r.promo_type, r.captured_at
            FROM rep r
            JOIN products p ON p.id = r.product_id
            JOIN categories c ON c.id = p.category_id
            JOIN chains ch ON ch.id = r.chain_id
            WHERE c.slug = $1
              AND ($2::text IS NULL OR ch.slug = $2)
            ORDER BY r.captured_at DESC, p.name, ch.slug
            LIMIT $3
            """,
            category_slug,
            chain_slug,
            limit,
        )
        return [
            CategoryPriceOut(
                id=r["id"],
                product_id=r["product_id"],
                product_name=r["product_name"],
                chain=r["chain"],
                chain_id=r["chain_id"],
                price=str(r["price"]),
                promo_type=r["promo_type"],
                captured_at=r["captured_at"],
            )
            for r in rows
        ]

    async def price_series_for_forecast(
        self,
        product_id: int,
        chain: str | None = None,
        *,
        limit: int = 365,
    ) -> list[tuple[date, Decimal]]:
        """Serie (fecha, precio) para los modelos de `app/ml`.

        Se agrega por día en SQL para no traer miles de filas cuando
        el scraper capturó varias veces el mismo producto. Sin `chain`
        mezcla todas las cadenas: la serie resultante es la del
        "precio de mercado" del producto.
        """
        rows = await self._provider.pool.fetch(
            """
            SELECT pp.captured_at::date AS day, AVG(pp.price) AS price
            FROM price_points pp
            JOIN chains ch ON ch.id = pp.chain_id
            WHERE pp.product_id = $1
              AND ($2::text IS NULL OR ch.slug = $2)
            GROUP BY pp.captured_at::date
            ORDER BY pp.captured_at::date
            LIMIT $3
            """,
            product_id,
            chain,
            limit,
        )
        return [(row["day"], Decimal(row["price"])) for row in rows]

    async def ml_candidates(self, min_points: int) -> list[tuple[int, int, str, int]]:
        """Pares (producto, cadena) con histórico suficiente para modelar.

        El job de entrenamiento (scripts/train_forecasts.py) itera sobre esto.
        Cuenta días distintos, no filas: un producto scraped 4 veces el mismo
        día es 1 punto de la serie, no 4 (mismo criterio que
        ``price_series_for_forecast``, que agrupa por día).
        """
        rows = await self._provider.pool.fetch(
            """
            SELECT pp.product_id,
                   ch.id AS chain_id,
                   ch.slug,
                   COUNT(DISTINCT pp.captured_at::date) AS dias
            FROM price_points pp
            JOIN chains ch ON ch.id = pp.chain_id
            WHERE ch.active
            GROUP BY pp.product_id, ch.id, ch.slug
            HAVING COUNT(DISTINCT pp.captured_at::date) >= $1
            ORDER BY dias DESC, pp.product_id, ch.slug
            """,
            min_points,
        )
        return [
            (r["product_id"], r["chain_id"], r["slug"], r["dias"])
            for r in rows
        ]

    async def save_forecast(
        self,
        *,
        product_id: int,
        chain_id: int,
        model: str,
        points: list[tuple[date, Decimal, Decimal, Decimal]],
        horizon_days: int = 1,
    ) -> int:
        """Persiste forecasts (idempotente: ON CONFLICT DO UPDATE)."""
        records = [
            (
                product_id,
                chain_id,
                model,
                forecast_date,
                predicted,
                conf_low,
                conf_high,
                horizon_days,
            )
            for forecast_date, predicted, conf_low, conf_high in points
        ]
        await self._provider.pool.executemany(
            """
            INSERT INTO forecasts (
                product_id, chain_id, model, forecast_date,
                predicted_price, conf_low, conf_high, horizon_days
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (product_id, chain_id, model, forecast_date)
            DO UPDATE SET predicted_price = EXCLUDED.predicted_price,
                          conf_low = EXCLUDED.conf_low,
                          conf_high = EXCLUDED.conf_high,
                          horizon_days = EXCLUDED.horizon_days,
                          trained_at = now()
            """,
            records,
        )
        return len(records)

    async def save_anomalies(
        self,
        *,
        product_id: int,
        chain_id: int,
        rows: list[tuple[datetime, Decimal, float]],
    ) -> int:
        """Persiste anomalías (idempotente por el UNIQUE de 008_ml.sql).

        ``price_point_id`` queda NULL: la serie de forecasting se agrega por
        día, así que un índice de la serie no mapea 1:1 a un price_point.
        """
        records = [
            (product_id, chain_id, captured_at, price, score)
            for captured_at, price, score in rows
        ]
        if not records:
            return 0
        await self._provider.pool.executemany(
            """
            INSERT INTO anomalies (product_id, chain_id, captured_at, price, score)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (product_id, chain_id, captured_at)
            DO UPDATE SET price = EXCLUDED.price,
                          score = EXCLUDED.score,
                          is_anomaly = true,
                          detected_at = now()
            """,
            records,
        )
        return len(records)

    async def save_ml_metrics(
        self,
        *,
        product_id: int,
        chain_id: int,
        model: str,
        language: str,
        mape: float,
        rmse: float,
        mae: float,
        n_points: int,
    ) -> int:
        """Registry de métricas por (model, lenguaje, producto, cadena).

        Es lo que lee .NET en ``/api/forecast/{id}/comparison`` para el
        comparativo del ADR-0006: la fila de Python (language='python') y la
        de .NET (language='dotnet') conviven en la misma tabla.
        """
        await self._provider.pool.execute(
            """
            INSERT INTO ml_models (
                model, language, product_id, chain_id,
                mape, rmse, mae, n_points, trained_at, is_active
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now(), true)
            ON CONFLICT (model, language, product_id, chain_id)
            DO UPDATE SET mape = EXCLUDED.mape,
                          rmse = EXCLUDED.rmse,
                          mae = EXCLUDED.mae,
                          n_points = EXCLUDED.n_points,
                          trained_at = now(),
                          is_active = true
            """,
            model,
            language,
            product_id,
            chain_id,
            mape,
            rmse,
            mae,
            n_points,
        )
        return 1



def _row_to_point(row: Any) -> PricePointOut:
    return PricePointOut(
        id=row["id"],
        product_id=row["product_id"],
        chain=row["chain"],
        chain_id=row["chain_id"],
        sku_id=row.get("sku_id"),
        store_id=row["store_id"],
        price=str(row["price"]),
        promo_type=row["promo_type"],
        captured_at=row["captured_at"],
        scraped_at=row["scraped_at"],
        hash=row["hash"],
    )


def _row_to_search(row: Any) -> ProductSearchOut:
    return ProductSearchOut(
        id=row["id"],
        name=row["name"],
        normalized_name=row["normalized_name"],
        brand=row.get("brand"),
        category=row.get("category"),
        category_slug=row.get("category_slug"),
        size_quantity=row.get("size_quantity"),
        size_unit=row.get("size_unit") or "und",
        ean=row.get("ean"),
        chain=row.get("chain", ""),
        chain_name=row.get("chain_name", ""),
        price=str(row.get("price", "")),
        promo_type=row.get("promo_type"),
        captured_at=row.get("captured_at") or datetime.now(UTC),
    )


class RedisReadiness:
    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Any | None = None

    @property
    def configured(self) -> bool:
        """READY solo exige Redis si fue configurado explícitamente (Fase 1)."""
        return bool(self._url)

    async def start(self) -> None:
        if self.configured:
            import redis.asyncio as aioredis  # lazy

            self._client = aioredis.from_url(self._url, decode_responses=True)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def publish_price_event(self, payload: dict[str, object]) -> None:
        """Publica un evento de precio en Redis (canal pl:events:price).

        Usado por el motor de alertas Java. Si Redis no est configured o falla
        la publicaci��n, se ignora: el scrape no debe fallar por un consumidor
        opcional.
        """
        if self._client is None:
            return
        try:
            import json

            await self._client.publish("pl:events:price", json.dumps(payload, default=str))
        except Exception:
            return