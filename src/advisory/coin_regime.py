"""Karsa Trading System - Per-Coin Regime Classifier (Deterministic)"""

import time
import asyncio
import json
from pydantic import BaseModel
from typing import Any
from src.utils.logging import get_logger
from src.advisory.crypto_regime import _adx
from src.advisory.crypto_technicals import calculate_bollinger

logger = get_logger("coin_regime")

class CoinRegime(BaseModel):
    symbol: str
    state: str
    adx_15m: float
    adx_4h: float
    adx_1d: float
    bbw_percentile_15m: float
    updated_at: float

class CoinRegimeEngine:
    def __init__(self, mcp_client: Any, cache: Any):
        self.mcp = mcp_client
        self.cache = cache
        
    async def get_regime(self, symbol: str) -> CoinRegime:
        """Get the current regime for a coin, calculating if missing from cache."""
        cache_key = f"karsa:state:regime:{symbol}"
        
        # Try redis cache first (assuming cache has get/set methods)
        try:
            # If cache is BybitClient.cache which is RedisClient
            if hasattr(self.cache, 'client'):
                cached = await self.cache.client.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    return CoinRegime(**data)
        except Exception as e:
            logger.debug("coin_regime_cache_miss", symbol=symbol, error=str(e))

        # Need to calculate
        try:
            # Fetch MTF data in parallel
            # limit=150 is enough for 120 bbw lookback + 20 period
            ohlcv_15m, ohlcv_4h, ohlcv_1d = await asyncio.gather(
                self.mcp.get_ohlcv(symbol, "CRYPTO", timeframe="15", limit=150),
                self.mcp.get_ohlcv(symbol, "CRYPTO", timeframe="240", limit=60),
                self.mcp.get_ohlcv(symbol, "CRYPTO", timeframe="D", limit=60),
            )

            adx_15m = self._calc_adx(ohlcv_15m)
            adx_4h = self._calc_adx(ohlcv_4h)
            adx_1d = self._calc_adx(ohlcv_1d)

            # BBW Percentile 15m (120 lookback)
            bbw_percentile = 100.0
            if ohlcv_15m:
                bb = calculate_bollinger(ohlcv_15m, period=20, std_dev=2.0, bbw_lookback=120)
                if bb.get("bbw_percentile") is not None:
                    bbw_percentile = bb["bbw_percentile"]

            # Determine State Matrix
            if adx_15m < 20 and adx_4h < 20 and adx_1d < 20:
                state = "DEAD_CHOP"
            elif bbw_percentile <= 10.0 and adx_4h > 20:
                state = "SQUEEZE_ALERT"
            elif adx_15m < 20 and adx_4h > 25 and adx_1d > 25:
                state = "MICRO_CHOP_IN_MACRO_TREND"
            elif adx_15m > 25 and adx_4h < 20 and adx_1d < 20:
                state = "MICRO_BREAKOUT"
            elif adx_15m > 25 and adx_4h > 25 and adx_1d > 25:
                state = "FULL_ALIGNMENT"
            else:
                if adx_4h > 20:
                    state = "TREND_BULL" if ohlcv_4h[-1]["close"] > ohlcv_4h[0]["close"] else "TREND_BEAR"
                else:
                    state = "CHOP"
            
            regime = CoinRegime(
                symbol=symbol,
                state=state,
                adx_15m=round(adx_15m, 1),
                adx_4h=round(adx_4h, 1),
                adx_1d=round(adx_1d, 1),
                bbw_percentile_15m=round(bbw_percentile, 1),
                updated_at=time.time()
            )

            # Save to redis
            try:
                if hasattr(self.cache, 'client'):
                    await self.cache.client.set(cache_key, json.dumps(regime.model_dump()), ex=900)
            except Exception as e:
                logger.warning("coin_regime_cache_write_failed", symbol=symbol, error=str(e))
                
            return regime

        except Exception as e:
            logger.error("coin_regime_failed", symbol=symbol, error=str(e))
            return CoinRegime(
                symbol=symbol, state="UNKNOWN", adx_15m=0, adx_4h=0, adx_1d=0, 
                bbw_percentile_15m=100.0, updated_at=time.time()
            )
            
    def _calc_adx(self, ohlcv: list[dict]) -> float:
        if not ohlcv or len(ohlcv) < 20:
            return 0.0
        highs = [c["high"] for c in ohlcv]
        lows = [c["low"] for c in ohlcv]
        closes = [c["close"] for c in ohlcv]
        return _adx(highs, lows, closes)
