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

    # Bybit (Crypto)
    BYBIT_API_KEY: str = ""
    BYBIT_API_SECRET: str = ""
    BYBIT_TESTNET: bool = True
    CRYPTO_TELEGRAM_TOKEN: str = ""
    CRYPTO_TELEGRAM_CHAT_ID: str = ""
    CRYPTO_MAX_RISK_PER_TRADE_PCT: float = 1.0
    CRYPTO_MAX_POSITION_PCT: float = 10.0
    CRYPTO_MAX_CONCURRENT_POSITIONS: int = 5
    CRYPTO_DAILY_LOSS_LIMIT_PCT: float = 3.0
    CRYPTO_MAX_EQUITY_DD_PCT: float = 15.0  # cumulative equity drawdown from peak
    CRYPTO_MAX_LEVERAGE: int = 10
    CRYPTO_FUNDING_ALERT_THRESHOLD: float = 0.05
    CRYPTO_FUNDING_HARD_REJECT_PCT: float = 0.05  # Gate 7: hard reject if funding > 0.05% per 8h
    CRYPTO_FUNDING_DRAG_MAX_PCT: float = 30.0     # Gate 7: max funding cost as % of target move
    CRYPTO_MAX_SL_PCT: float = 2.0  # max stop-loss distance from entry (2% default)
    CRYPTO_SL_MODE: str = "fixed"  # "atr" (ATR-based) or "fixed" (fixed dollar distance)
    CRYPTO_FIXED_SL_DISTANCE: float = 1.0  # dollar distance from entry when mode=fixed
    CRYPTO_LIQUIDATION_WARN_PCT: float = 20.0
    CRYPTO_LIQUIDATION_ALERT_PCT: float = 10.0
    CRYPTO_LIQUIDATION_FORCE_CLOSE_PCT: float = 5.0

    # Trading Safety Gate
    TRADING_MODE: str = "paper"  # "paper" | "live"

    # Crypto Separation
    CRYPTO_ONLY_MODE: bool = False  # True = skip IDX/US/ETF jobs

    # Trading Parameters
    MAX_PORTFOLIO_RISK_PCT: float = 2.0
    MAX_POSITION_SIZE_PCT: float = 15.0
    DAILY_LOSS_LIMIT_PCT: float = 5.0
    COST_MONTHLY_CEILING_USD: float = 300.0
    COST_DAILY_LIMIT_USD: float = 15.0

    # Risk Profile
    DEFAULT_RISK_PROFILE: str = "conservative"
    ENABLE_RISK_PROFILE_SWITCHING: bool = True

    # AODE (Asymmetric Opportunity Discovery Engine)
    AODE_ENABLED: bool = False
    AODE_DISCOVERY_INTERVAL_MIN: int = 60
    AODE_RESEARCH_BATCH_SIZE: int = 10
    AODE_MIN_COMPOSITE_SCORE: float = 50.0
    AODE_MAX_SECURITY_RISK: float = 80.0
    COINGECKO_API_KEY: str = ""
    GITHUB_TOKEN: str = ""
    ETHERSCAN_API_KEY: str = ""
    SOLSCAN_API_KEY: str = ""
    BSCSCAN_API_KEY: str = ""
    DEXSCREENER_ENABLED: bool = True

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

    @field_validator("TRADING_MODE")
    @classmethod
    def live_mode_requires_broker_keys(cls, v: str) -> str:
        if v == "live":
            import warnings
            warnings.warn(
                "TRADING_MODE='live' — ensure broker API keys (IDX_BROKER_TOKEN, US_BROKER_KEY) are configured",
                UserWarning,
            )
        return v

    @field_validator("TELEGRAM_TOKEN")
    @classmethod
    def telegram_token_should_be_set(cls, v: str) -> str:
        if not v:
            import warnings
            warnings.warn("TELEGRAM_TOKEN is empty — Telegram bot will not function")
        return v

    @property
    def redis_rate_limit_key(self) -> str:
        return f"{self.REDIS_PREFIX}:ratelimit"

    model_config = {
        "populate_by_name": True,
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore"
    }


# Global settings instance
settings = Settings()

# Resolved LLM config — 9ROUTER_* takes priority over ANTHROPIC_*
# NOTE: Anthropic SDK appends /v1/messages to base_url automatically.
# Strip trailing /v1 to avoid double /v1/v1/messages.
_raw_url = settings.NROUTER_BASE_URL
LLM_BASE_URL = _raw_url.rstrip("/").removesuffix("/v1")
LLM_AUTH_TOKEN = settings.NROUTER_AUTH_TOKEN
LLM_MODEL = settings.NROUTER_MODEL
