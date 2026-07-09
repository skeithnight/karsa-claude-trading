"""Karsa Trading System — Crypto Universe Configuration

Single source of truth for crypto trading universe.
Core list + dynamic top movers from Bybit (merged each scan cycle).
"""

from src.config import settings
from src.risk.crypto_risk_manager import CORRELATION_TIERS, MAX_LEVERAGE_BY_TIER, _get_tier
from src.utils.logging import get_logger

logger = get_logger("crypto_universe")

# Core universe: always scanned regardless of volume
CORE_UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
]

# Static fallback when Bybit API is unreachable
# NOTE: kept in rough tier order (tier1 -> tier2 -> tier3) for readability;
# actual tier assignment at runtime comes from _get_tier(), not list position.
CRYPTO_UNIVERSE = [
    # Tier 1 — majors
    "BTCUSDT", "ETHUSDT",
    # Tier 2 — large/mid caps
    "SOLUSDT", "BNBUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "NEARUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "TIAUSDT", "ATOMUSDT",
    "UNIUSDT", "AAVEUSDT", "LTCUSDT", "ETCUSDT", "BCHUSDT", "EGLDUSDT",
    "FETUSDT", "WLDUSDT", "RNDRUSDT", "TAOUSDT", "MKRUSDT",
    # Tier 3 — smaller cap / higher beta / meme
    "XRPUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT", "MATICUSDT", "TRXUSDT",
    "FILUSDT", "ICPUSDT", "EOSUSDT", "XLMUSDT", "VETUSDT", "ALGOUSDT",
    "FTMUSDT", "HBARUSDT", "THETAUSDT", "GALAUSDT", "SANDUSDT", "MANAUSDT",
    "APEUSDT", "AXSUSDT", "WIFUSDT", "BONKUSDT", "SHIBUSDT", "FLOKIUSDT",
    "BOMEUSDT", "PYTHUSDT", "STRKUSDT", "MANTLEUSDT", "AGIXUSDT",
    "OCEANUSDT", "CRVUSDT", "RUNEUSDT", "PENDLEUSDT", "ONDOUSDT", "SEIUSDT",
]

# Blacklist: tokens to never trade (illiquid, delisted, etc.)
UNIVERSE_BLACKLIST = set()

# Max total tokens per scan (core + dynamic)
MAX_UNIVERSE_SIZE = 50

# IMPORTANT: min_order_usd / tick_size below are reasonable placeholders,
# not live exchange data. Bybit tick sizes and min qty change over time —
# verify against GET /v5/market/instruments-info (category=linear) before
# relying on these for order placement, or better, fetch and cache them at
# startup instead of hardcoding. Wrong tick_size => order rejection risk.
PAIR_CONFIG = {
    # Tier 1
    "BTCUSDT":  {"min_order_usd": 10, "tick_size": 0.1, "category": "tier1"},
    "ETHUSDT":  {"min_order_usd": 10, "tick_size": 0.01, "category": "tier1"},
    # Tier 2
    "SOLUSDT":  {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "BNBUSDT":  {"min_order_usd": 5, "tick_size": 0.01, "category": "tier2"},
    "AVAXUSDT": {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "LINKUSDT": {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "SUIUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier2"},
    "NEARUSDT": {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "APTUSDT":  {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "ARBUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier2"},
    "OPUSDT":   {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier2"},
    "INJUSDT":  {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "TIAUSDT":  {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "ATOMUSDT": {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "UNIUSDT":  {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "AAVEUSDT": {"min_order_usd": 5, "tick_size": 0.01, "category": "tier2"},
    "LTCUSDT":  {"min_order_usd": 5, "tick_size": 0.01, "category": "tier2"},
    "ETCUSDT":  {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "BCHUSDT":  {"min_order_usd": 5, "tick_size": 0.01, "category": "tier2"},
    "EGLDUSDT": {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "FETUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier2"},
    "WLDUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier2"},
    "RNDRUSDT": {"min_order_usd": 5, "tick_size": 0.001, "category": "tier2"},
    "TAOUSDT":  {"min_order_usd": 5, "tick_size": 0.01, "category": "tier2"},
    "MKRUSDT":  {"min_order_usd": 5, "tick_size": 0.1, "category": "tier2"},
    # Tier 3
    "XRPUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "ADAUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "DOGEUSDT": {"min_order_usd": 5, "tick_size": 0.00001, "category": "tier3"},
    "DOTUSDT":  {"min_order_usd": 5, "tick_size": 0.001, "category": "tier3"},
    "MATICUSDT":{"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "TRXUSDT":  {"min_order_usd": 5, "tick_size": 0.00001, "category": "tier3"},
    "FILUSDT":  {"min_order_usd": 5, "tick_size": 0.001, "category": "tier3"},
    "ICPUSDT":  {"min_order_usd": 5, "tick_size": 0.001, "category": "tier3"},
    "EOSUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "XLMUSDT":  {"min_order_usd": 5, "tick_size": 0.00001, "category": "tier3"},
    "VETUSDT":  {"min_order_usd": 5, "tick_size": 0.00001, "category": "tier3"},
    "ALGOUSDT": {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "FTMUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "HBARUSDT": {"min_order_usd": 5, "tick_size": 0.00001, "category": "tier3"},
    "THETAUSDT":{"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "GALAUSDT": {"min_order_usd": 5, "tick_size": 0.00001, "category": "tier3"},
    "SANDUSDT": {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "MANAUSDT": {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "APEUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "AXSUSDT":  {"min_order_usd": 5, "tick_size": 0.001, "category": "tier3"},
    "WIFUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "BONKUSDT": {"min_order_usd": 5, "tick_size": 0.000001, "category": "tier3"},
    "SHIBUSDT": {"min_order_usd": 5, "tick_size": 0.000001, "category": "tier3"},
    "FLOKIUSDT":{"min_order_usd": 5, "tick_size": 0.000001, "category": "tier3"},
    "BOMEUSDT": {"min_order_usd": 5, "tick_size": 0.000001, "category": "tier3"},
    "PYTHUSDT": {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "STRKUSDT": {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "MANTLEUSDT":{"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "AGIXUSDT": {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "OCEANUSDT":{"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "CRVUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "RUNEUSDT": {"min_order_usd": 5, "tick_size": 0.001, "category": "tier3"},
    "PENDLEUSDT":{"min_order_usd": 5, "tick_size": 0.001, "category": "tier3"},
    "ONDOUSDT": {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
    "SEIUSDT":  {"min_order_usd": 5, "tick_size": 0.0001, "category": "tier3"},
}

SECTOR_MAPPING = {
    "BTCUSDT": "Layer1",
    "ETHUSDT": "Layer1",
    "SOLUSDT": "Layer1",
    "AVAXUSDT": "Layer1",
    "ADAUSDT": "Layer1",
    "DOTUSDT": "Layer1",
    "NEARUSDT": "Layer1",
    "SUIUSDT": "Layer1",
    "APTUSDT": "Layer1",
    "INJUSDT": "Layer1",
    "TIAUSDT": "Layer1",
    "SEIUSDT": "Layer1",
    "TRXUSDT": "Layer1",
    "EOSUSDT": "Layer1",
    "ICPUSDT": "Layer1",
    "ETCUSDT": "Layer1",
    "VETUSDT": "Layer1",
    "ALGOUSDT": "Layer1",
    "FTMUSDT": "Layer1",
    "HBARUSDT": "Layer1",
    "EGLDUSDT": "Layer1",
    "ATOMUSDT": "Layer1",
    "BNBUSDT": "Exchange",
    "XRPUSDT": "Payments",
    "LTCUSDT": "Payments",
    "BCHUSDT": "Payments",
    "XLMUSDT": "Payments",
    "DOGEUSDT": "Meme",
    "WIFUSDT": "Meme",
    "BONKUSDT": "Meme",
    "SHIBUSDT": "Meme",
    "FLOKIUSDT": "Meme",
    "BOMEUSDT": "Meme",
    "LINKUSDT": "Oracle",
    "PYTHUSDT": "Oracle",
    "MATICUSDT": "Layer2",
    "OPUSDT": "Layer2",
    "ARBUSDT": "Layer2",
    "STRKUSDT": "Layer2",
    "MANTLEUSDT": "Layer2",
    "FETUSDT": "AI",
    "AGIXUSDT": "AI",
    "WLDUSDT": "AI",
    "RNDRUSDT": "AI",
    "TAOUSDT": "AI",
    "OCEANUSDT": "AI",
    "UNIUSDT": "DeFi",
    "AAVEUSDT": "DeFi",
    "MKRUSDT": "DeFi",
    "CRVUSDT": "DeFi",
    "RUNEUSDT": "DeFi",
    "PENDLEUSDT": "DeFi",
    "ONDOUSDT": "RWA",
    "FILUSDT": "Storage",
    "THETAUSDT": "Media",
    "GALAUSDT": "Gaming",
    "SANDUSDT": "Gaming",
    "MANAUSDT": "Gaming",
    "APEUSDT": "Gaming",
    "AXSUSDT": "Gaming",
}


# --- Asset-class filter: exclude TradFi / xStock perpetuals ---
#
# Bybit's linear-perp universe is no longer crypto-only. Since May 2026 it
# also lists USDT-settled "TradFi" perpetuals tracking real equities/ETFs
# (TSLA, NVDA, META, BABA, SOXL, SNDK, CRWV, NBIS, QQQ, ...). These show up
# identically to crypto perps in get_all_perps()/get_top_movers(), but they
# track stock/ETF prices, not crypto — different volatility regime, gap risk
# when the underlying market is closed, dividend/corp-action exposure. None
# of that is what CORRELATION_TIERS / SECTOR_MAPPING were built for, so they
# must never enter the scored crypto universe.
#
# Bybit's official signal for this is the `symbolType` field/param on
# GET /v5/market/instruments-info (category=linear), with values including
# "stock", "forex", "commodity" alongside the default "crypto". Prefer
# filtering the request itself (symbolType=crypto) at the bybit_client layer
# if possible — the checks below are the in-process safety net for whatever
# get_all_perps()/get_top_movers() actually returns.
TRADFI_SYMBOL_TYPES = {"stock", "forex", "commodity", "equity", "etf"}

# Fallback symbol blacklist in case a candidate dict lacks symbolType
# metadata. Best-effort snapshot as of the May 2026 TradFi perps launch —
# WILL go stale as Bybit adds tickers. Treat symbolType as the source of
# truth; this is just a backstop.
TRADFI_STOCK_BLACKLIST = {
    "TSLAUSDT", "NVDAUSDT", "METAUSDT", "GOOGLUSDT", "MSFTUSDT", "ORCLUSDT",
    "AAPLUSDT", "INTCUSDT", "TSMUSDT", "MUUSDT", "SNDKUSDT", "MSTRUSDT",
    "COINUSDT", "CRCLUSDT", "HOODUSDT", "BABAUSDT", "SOXLUSDT", "CRWVUSDT",
    "NBISUSDT", "EWYUSDT", "EWJUSDT", "QQQUSDT",
}


def is_tradfi_perp(candidate: dict) -> bool:
    """True if a Bybit perp candidate is a tokenized-stock/ETF ("TradFi")
    contract rather than a crypto-native asset.
    """
    symbol_type = str(candidate.get("symbolType", "")).lower()
    if symbol_type and symbol_type in TRADFI_SYMBOL_TYPES:
        return True
    return candidate.get("symbol", "") in TRADFI_STOCK_BLACKLIST


def filter_tradfi_perps(candidates: list[dict]) -> list[dict]:
    """Strip tokenized-stock/ETF perpetuals out of a crypto candidate list."""
    kept, dropped = [], []
    for c in candidates:
        (dropped if is_tradfi_perp(c) else kept).append(c)
    if dropped:
        logger.info(
            "tradfi_perps_excluded",
            count=len(dropped),
            symbols=[c.get("symbol", "?") for c in dropped][:20],
        )
    return kept


# --- New-listing filter ---
#
# Bybit tags recently-listed pairs "NEW" (e.g. CRDOUSDT, MVLLUSDT). Freshly
# listed pairs routinely show extreme 24h moves purely from thin order books
# and unstable price discovery in their first days, not tradeable structure —
# left unfiltered they dominate "top movers" scans and score artificially
# well. Excluded from the scored universe until they've had time to settle;
# CORE_UNIVERSE symbols are exempt since they're always included regardless.
MIN_LISTING_AGE_DAYS = getattr(settings, "CRYPTO_MIN_LISTING_AGE_DAYS", 14)

# If a candidate has no discoverable launchTime, allow it through (current
# default) rather than silently shrinking the universe when the exchange
# omits the field for some symbols. Set CRYPTO_NEW_LISTING_FAIL_OPEN=False
# in settings for a more conservative bot that excludes unverifiable pairs.
NEW_LISTING_FAIL_OPEN = getattr(settings, "CRYPTO_NEW_LISTING_FAIL_OPEN", True)


def _get_launch_time_ms(candidate: dict) -> int | None:
    raw = candidate.get("launchTime")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def filter_new_listings(candidates: list[dict], min_age_days: int = MIN_LISTING_AGE_DAYS) -> list[dict]:
    """Exclude pairs listed more recently than min_age_days."""
    import time

    now_ms = int(time.time() * 1000)
    cutoff_ms = min_age_days * 24 * 60 * 60 * 1000

    kept, dropped, unknown_age = [], [], []

    for c in candidates:
        launch_ms = _get_launch_time_ms(c)
        if launch_ms is None:
            unknown_age.append(c.get("symbol", "?"))
            if NEW_LISTING_FAIL_OPEN:
                kept.append(c)
            continue

        if (now_ms - launch_ms) < cutoff_ms:
            dropped.append(c.get("symbol", "?"))
        else:
            kept.append(c)

    if dropped:
        logger.info("new_listings_excluded", count=len(dropped), min_age_days=min_age_days, symbols=dropped[:20])
    if unknown_age:
        logger.warning("listing_age_unknown", count=len(unknown_age), symbols=unknown_age[:20], fail_open=NEW_LISTING_FAIL_OPEN)

    return kept


async def get_dynamic_universe(bybit_client) -> list[str]:
    """Build scan universe: core tokens + top Bybit movers by volume.

    Returns up to MAX_UNIVERSE_SIZE symbols. Core tokens always included.
    Dynamic movers fill remaining slots, sorted by 24h turnover.
    Blacklisted, TradFi/xStock, and too-recently-listed tokens are excluded.
    """
    # Start with core (always scanned, exempt from all filters below)
    universe = list(CORE_UNIVERSE)

    try:
        movers = await bybit_client.get_top_movers(
            top_n=30,
            min_volume_usd=250_000,  # ponytail: matches UniverseEngine.generate() floor
        )
        movers = filter_tradfi_perps(movers)
        movers = filter_new_listings(movers)
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
            if len(universe) >= MAX_UNIVERSE_SIZE:
                break

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
UNIVERSE_TTL = 45 * 60  # 45 minutes

# Profile-aware universe sizes
_UNIVERSE_SIZE_BY_PROFILE = {
    "conservative": 12,
    "semi_aggressive": 20,
    "aggressive": 50,
}


class UniverseEngine:
    """Dynamic crypto universe generator. Scores, ranks, and caches top coins."""

    def __init__(self, bybit_client, redis_client, profile_manager=None):
        self._bybit = bybit_client
        self._redis = redis_client
        self._profile_mgr = profile_manager
        self._pubsub = None

    async def listen_profile_changes(self) -> None:
        """Subscribe to profile change events and regenerate universe immediately."""
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe("karsa:events:profile_changed")
        logger.info("universe_profile_listener_started")
        try:
            async for message in self._pubsub.listen():
                if message["type"] != "message":
                    continue
                import json
                event = json.loads(message["data"])
                logger.info("universe_profile_refresh", old=event.get("old"), new=event.get("new"))
                try:
                    await self.generate()
                except Exception as e:
                    logger.error("universe_profile_refresh_failed", error=str(e))
        except Exception as e:
            logger.error("universe_profile_listener_stopped", error=str(e))
        finally:
            await self._pubsub.unsubscribe()

    async def get_current(self) -> list[str]:
        """Read current universe from Redis. Falls back to static list."""
        try:
            raw = await self._redis.get(REDIS_UNIVERSE_KEY)
            if raw:
                import json
                return json.loads(raw)
            else:
                universe = await self.generate()
                if universe:
                    return universe
        except Exception as e:
            logger.warning("universe_read_failed", error=str(e))
        return list(CRYPTO_UNIVERSE)

    async def generate(self) -> list[str]:
        """Full pipeline: fetch → filter → score → rank → store → return.

        Uses risk profile to determine universe size.
        """
        import json
        import time
        from src.advisory.universe_scorer import filter_liquid, rank_candidates

        start = time.monotonic()

        # Determine universe size and volume floor from profile
        top_n = MAX_UNIVERSE_SIZE
        min_vol = 250_000  # absolute floor
        if self._profile_mgr:
            try:
                profile = await self._profile_mgr.get_active_profile()
                top_n = _UNIVERSE_SIZE_BY_PROFILE.get(profile.name, MAX_UNIVERSE_SIZE)
                min_vol = profile.min_volume_24h_usd
            except Exception:
                pass

        # Fetch all perps from Bybit (profile-aware volume floor)
        candidates = await self._bybit.get_all_perps(min_volume_usd=min_vol)
        if not candidates:
            logger.warning("universe_fetch_empty_fallback")
            try:
                from src.metrics.crypto_metrics import record_universe_refresh
                record_universe_refresh("failure", time.monotonic() - start)
            except Exception:
                pass
            return list(CRYPTO_UNIVERSE)

        # Strip out non-crypto TradFi/xStock perps and too-recently-listed
        # pairs before they can enter liquidity filtering / scoring.
        candidates = filter_tradfi_perps(candidates)
        candidates = filter_new_listings(candidates)

        # Filter by liquidity — use profile floor, fallback to $1M if too few
        liquid = filter_liquid(candidates, min_volume_usd=min_vol)
        if len(liquid) < 5:
            logger.info("universe_volume_fallback", strict_count=len(liquid), fallback_usd=1_000_000)
            liquid = filter_liquid(candidates, min_volume_usd=1_000_000)

        # Score and rank
        ranked = rank_candidates(
            liquid,
            top_n=top_n,
            min_score=20.0,
            always_include=set(CORE_UNIVERSE),
            sector_mapping=SECTOR_MAPPING,
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
                        "criteria": f"volume>=${min_vol/1_000_000:.0f}M,score>=20,top{top_n}",
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

        min_vol = 250_000
        if self._profile_mgr:
            try:
                profile = await self._profile_mgr.get_active_profile()
                min_vol = profile.min_volume_24h_usd
            except Exception:
                pass

        candidates = await self._bybit.get_all_perps(min_volume_usd=min_vol)
        if not candidates:
            return []

        candidates = filter_tradfi_perps(candidates)
        candidates = filter_new_listings(candidates)

        liquid = filter_liquid(candidates, min_volume_usd=min_vol)
        if len(liquid) < 5:
            liquid = filter_liquid(candidates, min_volume_usd=1_000_000)
        ranked = rank_candidates(liquid, top_n=MAX_UNIVERSE_SIZE, always_include=set(CORE_UNIVERSE), sector_mapping=SECTOR_MAPPING)
        return ranked