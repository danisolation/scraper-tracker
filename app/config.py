"""
Centralized configuration loaded from environment variables / .env file.
Uses pydantic-settings for validation and type coercion.
"""
import re
from pydantic_settings import BaseSettings
from pydantic import model_validator
from functools import lru_cache


class Settings(BaseSettings):
    # ── Database ───────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/scraper_tracker"

    # ── Telegram ───────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_DEFAULT_CHAT_ID: str = ""

    # ── Scheduler ──────────────────────────────────
    PRICE_CHECK_INTERVAL_MINUTES: int = 60

    # ── Scraper ────────────────────────────────────
    MAX_CONCURRENT_SCRAPES: int = 5
    REQUEST_TIMEOUT_SECONDS: int = 30

    @model_validator(mode="after")
    def normalize_database_url(self) -> "Settings":
        """
        Convert common Postgres URL prefixes to the asyncpg dialect
        that SQLAlchemy requires. Handles:
          postgres://  →  postgresql+asyncpg://
          postgresql:// → postgresql+asyncpg://
        """
        url = self.DATABASE_URL
        url = re.sub(r"^postgres://", "postgresql+asyncpg://", url)
        url = re.sub(r"^postgresql://", "postgresql+asyncpg://", url)
        self.DATABASE_URL = url
        return self

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


@lru_cache
def get_settings() -> Settings:
    """Singleton settings instance, cached after first call."""
    return Settings()
