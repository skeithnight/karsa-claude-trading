"""Karsa Trading System - Configuration Management"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # 9Router & LLM — reads 9ROUTER_* from env, falls back to ANTHROPIC_*
    NROUTER_ENABLED: bool = Field(default=True, alias="9ROUTER_ENABLED")
    NROUTER_BASE_URL: str = Field(default="", alias="9ROUTER_BASE_URL")
    NROUTER_AUTH_TOKEN: str = Field(default="", alias="9ROUTER_AUTH_TOKEN")
    NROUTER_MODEL: str = Field(default="", alias="9ROUTER_MODEL")

    ANTHROPIC_BASE_URL: str = "http://karsa-9router:20128/v1"
    ANTHROPIC_AUTH_TOKEN: str = "9router_internal_token"
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"

    # Database & State
    REDIS_URL: str = "redis://redis:6379"
    POSTGRES_URL: str = "postgresql://trader:changeme@postgres:5432/trading"
    DB_PASSWORD: str = "changeme"

    # Broker APIs
    IDX_BROKER_API_URL: str = "https://api.broker.co.id/v1"
    IDX_BROKER_TOKEN: str = ""
    US_BROKER_API_URL: str = "https://api.alpaca.markets/v2"
    US_BROKER_KEY: str = ""
    US_BROKER_SECRET: str = ""

    # Telegram
    TELEGRAM_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_WEBHOOK_URL: str | None = None  # If empty, uses long-polling mode
    TELEGRAM_WEBHOOK_SECRET: str = ""

    # Market Data
    TRADINGVIEW_MCP_URL: str = "http://tradingview-mcp:8080"
    IDX_DATA_API_URL: str = "https://api.stockbit.com/v1"
    IDX_DATA_API_KEY: str = ""

    # Trading Mode
    TRADING_MODE: str = "paper"  # "paper" = mock execution, "live" = real broker calls

    # Trading Parameters
    MAX_PORTFOLIO_RISK_PCT: float = 2.0
    MAX_POSITION_SIZE_PCT: float = 15.0
    DAILY_LOSS_LIMIT_PCT: float = 5.0
    COST_MONTHLY_CEILING_USD: float = 150.0
    COST_DAILY_LIMIT_USD: float = 10.0

    # Redis Keys
    REDIS_PREFIX: str = "karsa"

    @property
    def redis_rate_limit_key(self) -> str:
        return f"{self.REDIS_PREFIX}:ratelimit"

    model_config = {"populate_by_name": True, "env_file": ".env", "env_file_encoding": "utf-8"}


# Global settings instance
settings = Settings()

# Resolved LLM config — 9ROUTER_* takes priority over ANTHROPIC_*
LLM_BASE_URL = settings.NROUTER_BASE_URL or settings.ANTHROPIC_BASE_URL
LLM_AUTH_TOKEN = settings.NROUTER_AUTH_TOKEN or settings.ANTHROPIC_AUTH_TOKEN
LLM_MODEL = settings.NROUTER_MODEL or settings.ANTHROPIC_MODEL
