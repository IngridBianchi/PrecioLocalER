"""Fixtures compartidas para tests del scraper."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(chain: str, name: str) -> str:
    return (FIXTURES / chain / name).read_text(encoding="utf-8")


def load_json_fixture(chain: str, name: str) -> object:
    return json.loads(load_fixture(chain, name))


@pytest.fixture
def aceite_html() -> str:
    return load_fixture("la_anonima", "aceite.html")


@pytest.fixture
def harina_html() -> str:
    return load_fixture("la_anonima", "harina.html")


@pytest.fixture
def coto_aceite() -> object:
    return load_json_fixture("coto_digital", "aceite_girasol.json")


@pytest.fixture
def carrefour_aceites() -> object:
    return load_json_fixture("carrefour", "aceites_comunes.json")


@pytest.fixture
def changomas_arroz() -> object:
    """Página 1 real de `products/search/arroz` de MAS Online (50 items)."""
    return load_json_fixture("changomas", "arroz_0_49.json")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_environment="test",
        database_url="",
        redis_url="",
        scraper_min_request_interval=0,
        scraper_retries=1,
        la_anonima_categories="https://www.laanonima.com.ar/aceite/n3_604/",
        changomas_terms="arroz",
        changomas_results_per_page=50,
    )