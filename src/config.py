"""Karsa Trading System - Configuration Management"""

from pydantic import field_validator
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # 9Router & LLM — reads 9ROUTER_* from env, falls back to ANTHROPIC_*
    NROUTER_ENABLED: bool = Field(default=True, alias="9ROUTER_ENABLED")
    NROUTER_BASE_URL: str = Field(default="", alias="9ROUTER_BASE_URL")
    NROUTER_AUTH_TOKEN: str = Field(default="", alias="9ROUTER_AUTH_TOKEN")
    NROUTER_MODEL: str = Field(default="", alias="9ROUTER_MODEL")

    # Database & State
    REDIS_URL: str = "redis://redis:6379"
    POSTGRES_URL: str = "postgresql://trader:changeme@postgres:5432/trading"
    DB_PASSWORD: str = "changeme"

    # Telegram
    TELEGRAM_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_WEBHOOK_URL: str | None = None  # If empty, uses long-polling mode
    TELEGRAM_WEBHOOK_SECRET: str = ""

    # Market Data
    FINNHUB_API_KEY: str = ""
    MASSIVE_API_KEY: str = ""
    MASSIVE_BASE_URL: str = "https://api.massive.com/v3"

    # Trading Safety Gate
    TRADING_MODE: str = "paper"  # "paper" | "live"

    # Trading Parameters
    MAX_PORTFOLIO_RISK_PCT: float = 2.0
    MAX_POSITION_SIZE_PCT: float = 15.0
    DAILY_LOSS_LIMIT_PCT: float = 5.0
    COST_MONTHLY_CEILING_USD: float = 150.0
    COST_DAILY_LIMIT_USD: float = 10.0

    # Redis Keys
    REDIS_PREFIX: str = "karsa"

    @field_validator("DB_PASSWORD")
    @classmethod
    def password_must_be_set(cls, v: str) -> str:
        if not v or v.upper() in ("CHANGE_ME", "CHANGEME", "PASSWORD", "CHANGEME"):
            raise ValueError("DB_PASSWORD must be set to a real value — not a placeholder")
        if len(v) < 12:
            raise ValueError("DB_PASSWORD must be at least 12 characters")
        return v

    @field_validator("TRADING_MODE")
    @classmethod
    def valid_trading_mode(cls, v: str) -> str:
        if v not in ("paper", "live"):
            raise ValueError("TRADING_MODE must be 'paper' or 'live'")
        return v

    @property
    def redis_rate_limit_key(self) -> str:
        return f"{self.REDIS_PREFIX}:ratelimit"

    model_config = {"populate_by_name": True, "env_file": ".env", "env_file_encoding": "utf-8"}


# Global settings instance
settings = Settings()

# Resolved LLM config — 9ROUTER_* takes priority over ANTHROPIC_*
# NOTE: Anthropic SDK appends /v1/messages to base_url automatically.
# Strip trailing /v1 to avoid double /v1/v1/messages.
_raw_url = settings.NROUTER_BASE_URL
LLM_BASE_URL = _raw_url.rstrip("/").removesuffix("/v1")
LLM_AUTH_TOKEN = settings.NROUTER_AUTH_TOKEN
LLM_MODEL = settings.NROUTER_MODEL
