"""Karsa Trading System — Crypto Universe Configuration

Single source of truth for crypto trading universe.
Core list + dynamic top movers from Bybit (merged each scan cycle).
"""

from src.config import settings
from src.risk.crypto_risk_manager import CORRELATION_TIERS, MAX_LEVERAGE_BY_TIER, _get_tier

# Core universe: always scanned regardless of volume
CORE_UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
]

# Static fallback when Bybit API is unreachable
CRYPTO_UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "SUIUSDT", "NEARUSDT", "MATICUSDT", "PEPEUSDT",
]

# Blacklist: tokens to never trade (illiquid, delisted, etc.)
UNIVERSE_BLACKLIST = set()

# Max total tokens per scan (core + dynamic)
MAX_UNIVERSE_SIZE = 20

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


async def get_dynamic_universe(bybit_client) -> list[str]:
    """Build scan universe: core tokens + top Bybit movers by volume.

    Returns up to MAX_UNIVERSE_SIZE symbols. Core tokens always included.
    Dynamic movers fill remaining slots, sorted by 24h turnover.
    Blacklisted tokens are excluded.
    """
    # Start with core (always scanned)
    universe = list(CORE_UNIVERSE)

    try:
        movers = await bybit_client.get_top_movers(
            top_n=30,
            min_volume_usd=5_000_000,
        )
        for m in movers:
            sym = m["symbol"]
            if sym in UNIVERSE_BLACKLIST:
                continue
            if sym not in universe:
                universe.append(sym)
            if len(universe) >= MAX_UNIVERSE_SIZE:
                break
    except Exception:
        # Fallback to static list if Bybit unreachable
        for sym in CRYPTO_UNIVERSE:
            if sym not in universe:
                universe.append(sym)

    return universe


def get_max_leverage(symbol: str) -> int:
    """Get max leverage for a symbol based on its correlation tier."""
    tier = _get_tier(symbol)
    tier_max = MAX_LEVERAGE_BY_TIER.get(tier, 3)
    return min(tier_max, settings.CRYPTO_MAX_LEVERAGE)


def get_pair_config(symbol: str) -> dict:
    """Get full config for a symbol. Dynamic tokens get tier3 defaults."""
    tier = _get_tier(symbol)
    base = PAIR_CONFIG.get(symbol, {"min_order_usd": 5, "tick_size": 0.001, "category": "tier3"})
    return {
        "symbol": symbol,
        "tier": tier,
        "max_leverage": get_max_leverage(symbol),
        "min_order_usd": base["min_order_usd"],
        "tick_size": base["tick_size"],
    }
