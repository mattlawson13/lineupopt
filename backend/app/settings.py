"""Application settings, loaded from environment / .env.

Nothing configuration-shaped (scoring rules, roster rules, weights, sim
settings) lives here — those live in app/config/*.yaml and are loaded via
app/config/loader.py. This module is strictly process/deployment config.
"""
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    secret_key: str = "change-me"

    database_url: str = "sqlite:///./lineupopt.db"

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, v: str) -> str:
        # Managed Postgres providers (Render, Railway, Heroku-style) hand
        # out a plain "postgresql://..." URL, but we use psycopg3 — SQLAlchemy
        # needs the driver named explicitly in the scheme.
        if v.startswith("postgresql://") or v.startswith("postgres://"):
            return "postgresql+psycopg://" + v.split("://", 1)[1]
        return v
    redis_url: str = "redis://localhost:6379/0"

    dk_api_base_url: str = "https://api.draftkings.com"
    dk_site_base_url: str = "https://www.draftkings.com"
    dk_request_delay_seconds: float = 1.0

    nflverse_data_base_url: str = (
        "https://github.com/nflverse/nflverse-data/releases/download"
    )

    open_meteo_base_url: str = "https://api.open-meteo.com/v1"

    odds_api_key: str = ""
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"

    espn_api_base_url: str = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"

    fantasypros_api_key: str = ""
    anthropic_api_key: str = ""

    default_simulation_count: int = 10000
    max_simulation_count: int = 50000


@lru_cache
def get_settings() -> Settings:
    return Settings()
