"""Karsa Trading System — Crypto Universe Configuration

Single source of truth for crypto trading universe.
Eliminates duplication between analyst and orchestrator.
"""

from src.config import settings
from src.risk.crypto_risk_manager import CORRELATION_TIERS, MAX_LEVERAGE_BY_TIER, _get_tier

CRYPTO_UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "SUIUSDT", "NEARUSDT", "MATICUSDT", "PEPEUSDT"
]

PAIR_CONFIG = {
    "BTCUSDT":  {"min_order_usd": 10, "tick_size": 0.1, "category": "tier1"},
    "ETHUSDT":  {"min_order_usd": 10, "tick_size": 0.01, "category": "tier1"},
    "SOLUSDT":  {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "BNBUSDT":  {"min_order_usd": 5, "tick_size": 0.01, "category": "tier2"},
    "XRPUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "ADAUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "DOGEUSDT": {"min_order_usd": 5, "tick_size": 0.00001, "category": "tier3"},
    "AVAXUSDT": {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "DOTUSDT":  {"min_order_usd": 5, "tick_size": 0.001, "category": "tier3"},
    "LINKUSDT": {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "SUIUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier2"},
    "NEARUSDT": {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "MATICUSDT":{"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "PEPEUSDT": {"min_order_usd": 5, "tick_size": 0.0000001, "category": "tier3"},
}


def get_max_leverage(symbol: str) -> int:
    """Get max leverage for a symbol based on its correlation tier."""
    tier = _get_tier(symbol)
    tier_max = MAX_LEVERAGE_BY_TIER.get(tier, 3)
    return min(tier_max, settings.CRYPTO_MAX_LEVERAGE)


def get_pair_config(symbol: str) -> dict:
    """Get full config for a symbol."""
    tier = _get_tier(symbol)
    base = PAIR_CONFIG.get(symbol, {"min_order_usd": 5, "tick_size": 0.001, "category": "tier3"})
    return {
        "symbol": symbol,
        "tier": tier,
        "max_leverage": get_max_leverage(symbol),
        "min_order_usd": base["min_order_usd"],
        "tick_size": base["tick_size"],
    }
