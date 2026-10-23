"""Configuración central del servicio scraper via pydantic-settings."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # ---- Runtime ----
    app_environment: str = "development"
    log_level: str = "INFO"

    # ---- Conexiones ----
    database_url: str = ""
    redis_url: str = ""
    # Credencial del Redis por separado de la URL: el compose exige
    # `requirepass`, así que la URL queda sin usuario y la clave va aquí
    # (REDIS_PASSWORD). Se inyecta al construir el storage URI de slowapi.
    redis_password: str = ""

    # ---- CORS (orígenes permitidos separados por coma) ----
    cors_origins: str = "http://localhost:4200"

    # ---- Rate limiting (slowapi; checklist OWASP A04, docs/security.md §3) ----
    rate_limit_requests_per_minute: int = 30
    scrape_limit_per_user_hour: int = 5
    scrape_limit_per_chain_hour: int = 1

    # ---- Observabilidad (Fase 6) ----
    otel_exporter_otlp_endpoint: str = ""
    otel_exporter_otlp_headers: str = ""
    service_name: str = "preciolocal-scraper"

    # ---- ML (ADR-0006, WP2) ----
    # Horizonte por defecto de /ml/forecast y longitud estacional (7 = semana).
    ml_forecast_horizon_days: int = 7
    ml_season_length: int = 7
    # Fracción esperada de anomalías para el IsolationForest.
    ml_contamination: float = 0.05
    # Mínimo de price_points para intentar SARIMAX (debajo, fallback HW).
    ml_min_points_sarimax: int = 14

    # ---- Scraping (conservador por defecto, ver README/ADR-0007) ----
    # Nota 2026-09-23: cloudfront de laanonima.com.ar responde 403 a
    # user-agents no-navegador; usamos un UA estándar (configurable) pero
    # MANTENEMOS robots.txt + delays + bajo volumen.
    scraper_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    )
    scraper_min_request_interval: float = 2.0
    scraper_retries: int = 3
    scraper_timeout_seconds: float = 20.0
    scraper_circuit_failure_threshold: int = 3
    scraper_circuit_reset_timeout: float = 60.0
    robots_strict_on_failure: bool = True
    robots_cache_ttl_seconds: int = 86400

    # ---- La Anónima ----
    la_anonima_base_url: str = "https://www.laanonima.com.ar"
    # Listados de categorías (slugs/id tomados del sitemap-listados.xml; NO adivinar)
    la_anonima_categories: str = (
        "https://www.laanonima.com.ar/aceite/n3_604/,"
        "https://www.laanonima.com.ar/harina-de-trigo/n3_621/"
    )

    # ---- Coto Digital (SPA Angular + BFF públicos) ----
    coto_digital_api_base: str = "https://api.coto.com.ar/api/v1/ms-digital-sitio-bff-web/api/v1"
    # ⚠️ Sin default: la API key viaja en env (COTO_DIGITAL_API_KEY) y si falta
    # el adaptador falla explícito (fail-closed). Antes estaba hardcodeada.
    coto_digital_api_key: str = ""
    # Búsquedas por término (vía BFF products/search); el precio se toma de la
    # sucursal digital (store "200").
    coto_digital_search_terms: str = "aceite girasol,harina"
    coto_digital_results_per_page: int = 24

    # ---- Carrefour (VTEX catalog_system) ----
    carrefour_api_base: str = "https://www.carrefour.com.ar/api/catalog_system/pub/products/search"
    # Subcategorías acotadas (path real del sitio; ver resource total = volúmenes)
    carrefour_categories: str = (
        "almacen/aceites-y-vinagres/aceites-comunes,"
        "almacen/harinas/harinas-comunes-y-leudantes"
    )

    # ---- ChangoMás / MAS Online (VTEX catalog_system) ----
    # `changomas.com.ar` redirige a `masonline.com.ar`: mismo backend VTEX que
    # Carrefour pero otro catálogo (37.681 productos) y otro robots.txt.
    changomas_api_base: str = (
        "https://www.masonline.com.ar/api/catalog_system/pub/products/search"
    )
    # Búsquedas por término vía `products/search/{termino}`. El path es la única
    # forma de acotar el catálogo: `?q=` devuelve SIEMPRE la página 1 completa
    # (los 37.681) y `products/search/{slug-de-categoria}` devuelve 0 items
    # porque las categorías del árbol cuelgan de ids que la búsqueda no acepta.
    # Verificado 2026-10-01 sobre la API real; ver app/scrapers/changomas.py.
    changomas_terms: str = "aceite,arroz,harina,leche,yerba,gaseosa"
    changomas_results_per_page: int = 50

    # ---- Admin (POST /scrape/run) ----
    admin_token: str = ""

    @property
    def category_urls(self) -> list[str]:
        return [u.strip() for u in self.la_anonima_categories.split(",") if u.strip()]

    @property
    def coto_digital_terms(self) -> list[str]:
        return [t.strip() for t in self.coto_digital_search_terms.split(",") if t.strip()]

    @property
    def carrefour_paths(self) -> list[str]:
        return [p.strip() for p in self.carrefour_categories.split(",") if p.strip()]

    @property
    def changomas_search_terms(self) -> list[str]:
        return [t.strip() for t in self.changomas_terms.split(",") if t.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()