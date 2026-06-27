"""Karsa Trading System - Macro Regime Filter

Split by market:
- USRegimeFilter: SPY vs 200-SMA, VIX (optional)
- IDXRegimeFilter: IHSG vs 200-SMA, BBCA trend as proxy
"""

import time
from typing import Any

from src.utils.logging import get_logger

logger = get_logger("regime")

# Per-market in-memory cache
_regime_cache: dict[str, dict] = {}
_regime_cache_ttl = 300  # 5 minutes


def _get_cached(market: str) -> dict | None:
    entry = _regime_cache.get(market)
    if entry and time.time() - entry.get("ts", 0) < _regime_cache_ttl:
        return entry["data"]
    return None


def _set_cached(market: str, data: dict):
    _regime_cache[market] = {"data": data, "ts": time.time()}


def _size_multiplier(regime: str) -> float:
    if regime == "BULL":
        return 1.2
    elif regime == "BEAR":
        return 0.5
    return 1.0


class USRegimeFilter:
    """US market regime: SPY vs 200-SMA, VIX as sentiment gauge."""

    def __init__(self, mcp_client: Any):
        self.mcp = mcp_client

    async def get_current_regime(self) -> dict:
        cached = _get_cached("US")
        if cached:
            return cached

        try:
            spy_quote = await self.mcp.get_quote("SPY", "US")
            spy_price = spy_quote.get("price", 0.0) if not spy_quote.get("error") else 0.0

            spy_sma200 = 0.0
            try:
                spy_sma200_data = await self.mcp.get_ema("SPY", "US", 200)
                spy_sma200 = float(spy_sma200_data) if spy_sma200_data else 0.0
            except Exception:
                pass

            # VIX is a CBOE index — TradingView may not resolve it.
            # Default to 18.0 (historically neutral) when unavailable.
            vix_price = 18.0
            try:
                vix_quote = await self.mcp.get_quote("VIX", "US")
                if not vix_quote.get("error") and vix_quote.get("price"):
                    vix_price = float(vix_quote["price"])
            except Exception:
                pass

            if spy_price == 0.0:
                return self._unknown("SPY price unavailable")

            if spy_sma200 == 0.0:
                spy_sma200 = spy_price  # Treat as "at SMA" -> NEUTRAL

            if vix_price < 20 and spy_price > spy_sma200:
                regime = "BULL"
                rec = "Aggressive long bias. Full position sizing."
            elif vix_price > 25 or spy_price < spy_sma200:
                regime = "BEAR"
                rec = "Defensive mode. Cut position sizes by 50%. Increase cash."
            else:
                regime = "NEUTRAL"
                rec = "Standard conditions. Normal position sizing."

            result = {
                "state": regime,
                "benchmark": "SPY",
                "benchmark_price": spy_price,
                "sma200": spy_sma200,
                "vix": round(vix_price, 2),
                "recommendation": rec,
                "size_multiplier": _size_multiplier(regime),
            }
            _set_cached("US", result)
            return result

        except Exception as e:
            logger.error("us_regime_failed", error=str(e))
            cached = _regime_cache.get("US")
            if cached:
                return cached["data"]
            return self._unknown(str(e))

    def _unknown(self, reason: str) -> dict:
        return {
            "state": "UNKNOWN",
            "benchmark": "SPY",
            "benchmark_price": "N/A",
            "sma200": "N/A",
            "vix": "N/A",
            "recommendation": f"Data unavailable: {reason}",
            "size_multiplier": 1.0,
        }


class IDXRegimeFilter:
    """IDX market regime: IHSG vs 200-SMA, BBCA as liquidity proxy.

    No VIX equivalent for IDX — regime is purely trend-based.
    Falls back to BBCA if IHSG is unreachable (TradingView index limitation).
    """

    def __init__(self, mcp_client: Any):
        self.mcp = mcp_client

    async def get_current_regime(self) -> dict:
        cached = _get_cached("IDX")
        if cached:
            return cached

        try:
            # Try IHSG (Indonesia Composite Index) first
            ihsg_price = 0.0
            benchmark = "IHSG"
            try:
                ihsg_quote = await self.mcp.get_quote("IHSG", "IDX")
                ihsg_price = ihsg_quote.get("price", 0.0) if not ihsg_quote.get("error") else 0.0
            except Exception:
                pass

            # Fallback: use BBCA as market proxy (most liquid IDX stock)
            if ihsg_price == 0.0:
                benchmark = "BBCA"
                try:
                    bbca_quote = await self.mcp.get_quote("BBCA", "IDX")
                    ihsg_price = bbca_quote.get("price", 0.0) if not bbca_quote.get("error") else 0.0
                except Exception:
                    pass

            if ihsg_price == 0.0:
                return self._unknown("IHSG and BBCA both unavailable")

            # Get 200 SMA for the benchmark
            sma200 = 0.0
            try:
                sma200_data = await self.mcp.get_ema(benchmark, "IDX", 200)
                sma200 = float(sma200_data) if sma200_data else 0.0
            except Exception:
                pass

            if sma200 == 0.0:
                sma200 = ihsg_price  # Treat as "at SMA" -> NEUTRAL

            # BBCA as secondary confirmation
            bbca_price = 0.0
            try:
                bbca_quote = await self.mcp.get_quote("BBCA", "IDX")
                bbca_price = bbca_quote.get("price", 0.0) if not bbca_quote.get("error") else 0.0
            except Exception:
                pass

            if ihsg_price > sma200:
                regime = "BULL"
                rec = "IDX uptrend. Normal to aggressive long sizing."
            elif ihsg_price < sma200 * 0.97:  # 3% below SMA = clear bear
                regime = "BEAR"
                rec = "IDX downtrend. Reduce position sizes. Increase cash."
            else:
                regime = "NEUTRAL"
                rec = "IDX near SMA. Standard conditions, selective entries."

            result = {
                "state": regime,
                "benchmark": benchmark,
                "benchmark_price": ihsg_price,
                "sma200": sma200,
                "bbca_price": bbca_price if bbca_price else "N/A",
                "recommendation": rec,
                "size_multiplier": _size_multiplier(regime),
            }
            _set_cached("IDX", result)
            return result

        except Exception as e:
            logger.error("idx_regime_failed", error=str(e))
            cached = _regime_cache.get("IDX")
            if cached:
                return cached["data"]
            return self._unknown(str(e))

    def _unknown(self, reason: str) -> dict:
        return {
            "state": "UNKNOWN",
            "benchmark": "IHSG",
            "benchmark_price": "N/A",
            "sma200": "N/A",
            "bbca_price": "N/A",
            "recommendation": f"Data unavailable: {reason}",
            "size_multiplier": 1.0,
        }


# Backward-compatible alias
MacroRegimeFilter = USRegimeFilter
