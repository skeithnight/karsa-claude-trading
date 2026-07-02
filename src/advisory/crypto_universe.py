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


# --- Dynamic Universe Engine ---

REDIS_UNIVERSE_KEY = "karsa:state:crypto_universe"
UNIVERSE_TTL = 4 * 3600  # 4 hours

# Profile-aware universe sizes
_UNIVERSE_SIZE_BY_PROFILE = {
    "conservative": 8,
    "semi_aggressive": 12,
    "aggressive": 15,
}


class UniverseEngine:
    """Dynamic crypto universe generator. Scores, ranks, and caches top coins."""

    def __init__(self, bybit_client, redis_client, profile_manager=None):
        self._bybit = bybit_client
        self._redis = redis_client
        self._profile_mgr = profile_manager

    async def get_current(self) -> list[str]:
        """Read current universe from Redis. Falls back to static list."""
        try:
            raw = await self._redis.get(REDIS_UNIVERSE_KEY)
            if raw:
                import json
                return json.loads(raw)
        except Exception:
            pass
        return list(CRYPTO_UNIVERSE)

    async def generate(self) -> list[str]:
        """Full pipeline: fetch → filter → score → rank → store → return.

        Uses risk profile to determine universe size.
        """
        import json
        import time
        from src.advisory.universe_scorer import filter_liquid, rank_candidates

        start = time.monotonic()

        # Determine universe size from profile
        top_n = MAX_UNIVERSE_SIZE
        if self._profile_mgr:
            try:
                profile_name = await self._profile_mgr.get_active_profile_name()
                top_n = _UNIVERSE_SIZE_BY_PROFILE.get(profile_name, MAX_UNIVERSE_SIZE)
            except Exception:
                pass

        # Fetch all perps from Bybit
        candidates = await self._bybit.get_all_perps(min_volume_usd=1_000_000)
        if not candidates:
            logger.warning("universe_fetch_empty_fallback")
            try:
                from src.metrics.crypto_metrics import record_universe_refresh
                record_universe_refresh("failure", time.monotonic() - start)
            except Exception:
                pass
            return list(CRYPTO_UNIVERSE)

        # Filter by liquidity
        liquid = filter_liquid(candidates, min_volume_usd=5_000_000)

        # Score and rank
        ranked = rank_candidates(
            liquid,
            top_n=top_n,
            min_score=20.0,
            always_include=set(CORE_UNIVERSE),
        )

        universe = [c["symbol"] for c in ranked]
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Store in Redis with TTL
        try:
            await self._redis.setex(REDIS_UNIVERSE_KEY, UNIVERSE_TTL, json.dumps(universe))
        except Exception as e:
            logger.error("universe_redis_write_failed", error=str(e))

        # Log to DB (best effort)
        try:
            from src.models.database import async_session
            from sqlalchemy import text
            profile_name = "conservative"
            if self._profile_mgr:
                try:
                    profile_name = await self._profile_mgr.get_active_profile_name()
                except Exception:
                    pass
            async with async_session() as session:
                await session.execute(
                    text(
                        "INSERT INTO universe_history (universe_json, coin_count, selection_criteria, risk_profile, refresh_duration_ms) "
                        "VALUES (:data, :count, :criteria, :profile, :ms)"
                    ),
                    {
                        "data": json.dumps(universe),
                        "count": len(universe),
                        "criteria": f"volume>=5M,score>=20,top{top_n}",
                        "profile": profile_name,
                        "ms": elapsed_ms,
                    },
                )
                await session.commit()
        except Exception as e:
            logger.warning("universe_db_log_failed", error=str(e))

        logger.info("universe_generated", count=len(universe), ms=elapsed_ms, coins=universe[:5])

        # Record Prometheus metrics
        try:
            from src.metrics.crypto_metrics import update_universe_size, record_universe_refresh, update_coin_score
            update_universe_size(len(universe))
            record_universe_refresh("success", elapsed_ms / 1000)
            for c in ranked:
                update_coin_score(c["symbol"], c["score"])
        except Exception:
            pass

        return universe

    async def get_universe_with_scores(self) -> list[dict]:
        """Get current universe with scoring details for display."""
        from src.advisory.universe_scorer import score_candidate, filter_liquid, rank_candidates

        candidates = await self._bybit.get_all_perps(min_volume_usd=1_000_000)
        if not candidates:
            return []

        liquid = filter_liquid(candidates, min_volume_usd=5_000_000)
        ranked = rank_candidates(liquid, top_n=MAX_UNIVERSE_SIZE, always_include=set(CORE_UNIVERSE))
        return ranked
