"""Karsa Trading System - Crypto Regime Classifier (Deterministic)

Pure Python — LLM is forbidden from guessing the regime.
Uses Hurst Exponent (trend persistence) and ADX (trend strength) on BTC.

Regime states:
- TREND_BULL: H > 0.5, ADX > 25, price > 200 EMA
- TREND_BEAR: H > 0.5, ADX > 25, price < 200 EMA
- MEAN_REVERSION: H < 0.5 (price tends to revert)
- CHOP: ADX < 20 (no clear trend)

BTC Dominance: CoinGecko free API
- >55% = BTC season (favor BTC/ETH), <45% = alt season (spread across alts)
"""

import asyncio
import time
from typing import Any

from src.utils.logging import get_logger
from src.advisory.crypto_technicals import calculate_bollinger

logger = get_logger("crypto_regime")

_regime_cache: dict[str, dict] = {}
_regime_cache_ttl = 300  # 5 minutes
_adx_cache: dict[str, dict] = {}


def _get_cached() -> dict | None:
    entry = _regime_cache.get("CRYPTO")
    if entry and time.time() - entry.get("ts", 0) < _regime_cache_ttl:
        return entry["data"]
    return None


def _set_cached(data: dict):
    old = _regime_cache.get("CRYPTO", {}).get("data", {})
    old_state = old.get("state")
    new_state = data.get("state")
    _regime_cache["CRYPTO"] = {"data": data, "ts": time.time()}
    # Fire-and-forget transition alert
    if old_state and new_state and old_state != new_state:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_alert_regime_transition(old_state, new_state, data))
        except RuntimeError:
            pass  # No event loop running


async def _alert_regime_transition(old_state: str, new_state: str, regime: dict) -> None:
    """Send Telegram alert on regime transition."""
    try:
        import httpx
        from src.config import settings

        token = settings.CRYPTO_TELEGRAM_TOKEN or settings.TELEGRAM_TOKEN
        chat_id_str = None
        try:
            # Finding 11: use asyncio.to_thread to avoid blocking the event loop
            import redis.asyncio as async_redis
            r = async_redis.from_url(settings.REDIS_URL, decode_responses=True)
            try:
                chat_id_str = await r.get("karsa:telegram_chat_id")
            finally:
                await r.close()
        except Exception:
            pass

        if not token or not chat_id_str:
            return

        size_mult = regime.get("size_multiplier", "N/A")
        message = (
            f"🔄 <b>Regime Transition</b>\n"
            f"  <b>From:</b> {old_state}\n"
            f"  <b>To:</b> {new_state}\n"
            f"  <b>Size Multiplier:</b> {size_mult}\n"
            f"  <b>BTC:</b> ${regime.get('benchmark_price', 'N/A'):,.2f}"
        )

        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": int(chat_id_str), "text": message, "parse_mode": "HTML"},
            )
        logger.info("regime_transition_alert_sent", old=old_state, new=new_state)
    except Exception as e:
        logger.warning("regime_transition_alert_failed", error=str(e))


def _hurst_exponent(prices: list[float]) -> float:
    """Estimate Hurst Exponent using Rescaled Range (R/S) method.

    H > 0.5: trending (persistent)
    H = 0.5: random walk
    H < 0.5: mean-reverting (anti-persistent)
    """
    if len(prices) < 20:
        return 0.5

    import math
    returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
    if len(returns) < 10:
        return 0.5

    window_sizes = [10, 20, 40, 60]
    rs_values = []

    for n in window_sizes:
        if n > len(returns):
            continue

        rs_list = []
        for start in range(0, len(returns) - n + 1, n):
            sub = returns[start:start + n]
            mean_r = sum(sub) / len(sub)
            deviations = [r - mean_r for r in sub]
            cumulative = []
            cumsum = 0.0
            for d in deviations:
                cumsum += d
                cumulative.append(cumsum)

            R = max(cumulative) - min(cumulative)
            S = (sum(r ** 2 for r in sub) / len(sub)) ** 0.5

            if S > 0:
                rs_list.append(R / S)

        if rs_list:
            rs_values.append((n, sum(rs_list) / len(rs_list)))

    if len(rs_values) < 2:
        return 0.5

    log_n = [math.log(n) for n, _ in rs_values]
    log_rs = [math.log(max(rs, 0.001)) for _, rs in rs_values]

    n_points = len(log_n)
    sum_x = sum(log_n)
    sum_y = sum(log_rs)
    sum_xy = sum(x * y for x, y in zip(log_n, log_rs))
    sum_x2 = sum(x ** 2 for x in log_n)

    denominator = n_points * sum_x2 - sum_x ** 2
    if denominator == 0:
        return 0.5

    hurst = (n_points * sum_xy - sum_x * sum_y) / denominator
    return max(0.0, min(1.0, hurst))


def _adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Calculate Average Directional Index (ADX).

    ADX > 25: strong trend
    ADX < 20: weak/no trend (chop)
    """
    if len(highs) < period + 1:
        return 0.0

    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(1, len(highs)):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))

        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)

    if len(tr_list) < period:
        return 0.0

    def wilder_smooth(data: list[float], p: int) -> list[float]:
        result = [sum(data[:p])]
        for i in range(p, len(data)):
            result.append(result[-1] - result[-1] / p + data[i])
        return [v / p for v in result]

    atr = wilder_smooth(tr_list, period)
    smooth_plus = wilder_smooth(plus_dm, period)
    smooth_minus = wilder_smooth(minus_dm, period)

    plus_di = [100 * p / a if a > 0 else 0 for p, a in zip(smooth_plus, atr)]
    minus_di = [100 * m / a if a > 0 else 0 for m, a in zip(smooth_minus, atr)]

    dx = []
    for pd, md in zip(plus_di, minus_di):
        total = pd + md
        dx.append(100 * abs(pd - md) / total if total > 0 else 0)

    if len(dx) < period:
        return 0.0

    adx_vals = wilder_smooth(dx, period)
    return adx_vals[-1] if adx_vals else 0.0


def _size_multiplier(regime: str) -> float:
    return {
        "FULL_TREND_ALIGNMENT": 1.0,
        "MACRO_BULL_MICRO_PULLBACK": 0.8,
        "MACRO_BEAR_MICRO_PULLBACK": 0.8,
        "MICRO_BREAKOUT_NO_MACRO": 0.5,
        "PURE_DEAD_CHOP": 0.0,
        "MEAN_REVERSION": 0.5,
    }.get(regime, 0.5)

_BTC_DOM_REDIS_KEY = "karsa:regime:btc_dominance"
_BTC_DOM_TTL = 3600  # 1 hour — CoinGecko free tier safe; dominance doesn't shift fast


async def _get_btc_dominance(redis_client=None) -> dict:
    """Get BTC dominance, Redis-cached for 1 hour.

    Accepts an optional redis_client to use for caching. Falls back to
    direct HTTP fetch with a 5s timeout (was 10s in the original).
    Returns: {"btc_dominance": float, "season": str, "eth_dominance": float}
    """
    import json

    # Try Redis cache first — avoids HTTP call on every 5-min regime refresh
    if redis_client:
        try:
            cached = await redis_client.get(_BTC_DOM_REDIS_KEY)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    # Fetch from CoinGecko
    try:
        import urllib.request

        url = "https://api.coingecko.com/api/v3/global"
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "karsa/1.0"})

        def _fetch():
            with urllib.request.urlopen(req, timeout=5) as resp:  # 5s, was 10s
                return json.loads(resp.read())

        data = await asyncio.to_thread(_fetch)
        market_data = data.get("data", {})
        btc_dom = market_data.get("market_cap_percentage", {}).get("btc", 0)
        eth_dom = market_data.get("market_cap_percentage", {}).get("eth", 0)

        if btc_dom > 55:
            season = "BTC_SEASON"
        elif btc_dom < 45:
            season = "ALT_SEASON"
        else:
            season = "NEUTRAL"

        result = {
            "btc_dominance": round(btc_dom, 2),
            "eth_dominance": round(eth_dom, 2),
            "season": season,
        }

        # Cache in Redis so the next 11 calls (within 1h) are free
        if redis_client:
            try:
                await redis_client.set(_BTC_DOM_REDIS_KEY, json.dumps(result), ex=_BTC_DOM_TTL)
            except Exception:
                pass

        return result

    except Exception as e:
        logger.warning("btc_dominance_fetch_failed", error=str(e))
        return {"btc_dominance": None, "eth_dominance": None, "season": "UNKNOWN"}


class CryptoRegimeFilter:
    """Deterministic crypto regime classifier using BTC as benchmark."""

    def __init__(self, mcp_client: Any, redis_client=None):
        self.mcp = mcp_client
        self._redis = redis_client  # Optional: used to cache CoinGecko call in Redis

    async def _get_cached_adx(self, symbol: str, timeframe: str, ttl: int) -> float:
        cache_key = f"{symbol}_{timeframe}"
        entry = _adx_cache.get(cache_key)
        if entry and time.time() - entry.get("ts", 0) < ttl:
            return entry["adx"]
        
        # Cache miss - fetch OHLCV and calculate ADX
        ohlcv = await self.mcp.get_ohlcv(symbol, "CRYPTO", timeframe=timeframe, limit=200)
        if not ohlcv or len(ohlcv) < 20:
            return 0.0
            
        highs = [c["high"] for c in ohlcv]
        lows = [c["low"] for c in ohlcv]
        closes = [c["close"] for c in ohlcv]
        
        adx_val = _adx(highs, lows, closes)
        _adx_cache[cache_key] = {"adx": adx_val, "ts": time.time()}
        return adx_val

    async def get_current_regime(self) -> dict:
        cached = _get_cached()
        if cached:
            return cached

        try:
            btc_ohlcv_4h = await self.mcp.get_ohlcv("BTCUSDT", "CRYPTO", timeframe="4h", limit=200)
            if not btc_ohlcv_4h or len(btc_ohlcv_4h) < 60:
                logger.warning("crypto_regime_insufficient_data", count=len(btc_ohlcv_4h) if btc_ohlcv_4h else 0)
                return self._unknown(f"Insufficient BTC data ({len(btc_ohlcv_4h) if btc_ohlcv_4h else 0} candles, need 60+)")

            closes_4h = [c["close"] for c in btc_ohlcv_4h]
            hurst = _hurst_exponent(closes_4h)
            btc_price = closes_4h[-1] if closes_4h else 0
            # EMA200 with proper exponential smoothing (k = 2/(period+1))
            if len(closes_4h) >= 200:
                k = 2.0 / (200 + 1)
                ema_200 = closes_4h[0]
                for price in closes_4h[1:]:
                    ema_200 = price * k + ema_200 * (1 - k)
            elif closes_4h:
                # Fallback to SMA if insufficient data for proper EMA
                ema_200 = sum(closes_4h) / len(closes_4h)
            else:
                ema_200 = btc_price

            # MTF ADX Contextual Regime Engine
            adx_15m = await self._get_cached_adx("BTCUSDT", "15", 1200)
            adx_4h = await self._get_cached_adx("BTCUSDT", "4h", 18000)
            adx_1d = await self._get_cached_adx("BTCUSDT", "1D", 90000)

            macro_trending = (adx_4h > 25 and adx_1d > 25)
            micro_trending = (adx_15m > 25)

            if macro_trending and micro_trending:
                regime = "FULL_TREND_ALIGNMENT"
                rec = "Pure explosive trend. Max sizing. Buy breakouts."
            elif macro_trending and not micro_trending and btc_price > ema_200:
                regime = "MACRO_BULL_MICRO_PULLBACK"
                rec = "Macro trend UP, micro resting. Buy the dip."
            elif macro_trending and not micro_trending and btc_price <= ema_200:
                regime = "MACRO_BEAR_MICRO_PULLBACK"
                rec = "Macro trend DOWN, micro resting. Short the rally."
            elif not macro_trending and micro_trending:
                regime = "MICRO_BREAKOUT_NO_MACRO"
                rec = "Micro breakout without macro support. High risk, half size."
            else:
                if hurst < 0.45:
                    regime = "MEAN_REVERSION"
                    rec = "Mean-reverting conditions. Fade extremes. Smaller positions."
                else:
                    regime = "PURE_DEAD_CHOP"
                    rec = "Dead chop across all timeframes. Do not trade."

            # Fetch BTC dominance — Redis-cached for 1h to avoid blocking scan loop
            btc_dom = await _get_btc_dominance(redis_client=self._redis)

            # ETH/BTC ratio — key signal for alt rotation
            eth_btc_ratio = None
            try:
                eth_ohlcv = await self.mcp.get_ohlcv("ETHUSDT", "CRYPTO", timeframe="4h", limit=30)
                if eth_ohlcv and closes_4h and len(eth_ohlcv) >= 7:
                    eth_closes = [c["close"] for c in eth_ohlcv]
                    eth_btc_ratio = eth_closes[-1] / btc_price if btc_price > 0 else None
            except Exception:
                pass

            # Fear & Greed Index — Redis-cached 1hr
            fear_greed = None
            try:
                if self._redis:
                    fg_cached = await self._redis.get("karsa:fear_greed_index")
                    if fg_cached:
                        fear_greed = int(fg_cached)
                if fear_greed is None:
                    import httpx
                    async with httpx.AsyncClient(timeout=5) as client:
                        fg_resp = await client.get("https://api.alternative.me/fng/?limit=1")
                        if fg_resp.status_code == 200:
                            fg_data = fg_resp.json().get("data", [{}])[0]
                            fear_greed = int(fg_data.get("value", 50))
                            if self._redis:
                                await self._redis.setex("karsa:fear_greed_index", 3600, str(fear_greed))
            except Exception:
                pass

            # Funding rate regime — sustained patterns
            funding_regime = "NEUTRAL"
            try:
                # Fetch recent funding history for BTC
                if self.mcp:
                    bybit = self.mcp._get_bybit()
                    if bybit:
                        funding_history = await bybit.get_funding_history("BTCUSDT", limit=6)
                        if funding_history and len(funding_history) >= 3:
                            rates = [float(h.get("fundingRate", 0)) for h in funding_history]
                            avg_rate = sum(rates) / len(rates)
                            if avg_rate < -0.0005:
                                funding_regime = "SHORT_CROWDING"
                            elif avg_rate > 0.0005:
                                funding_regime = "LONG_CROWDING"
            except Exception:
                pass

            # Volatility regime from BTC 4H BBW percentile
            try:
                bb = calculate_bollinger(btc_ohlcv_4h, period=20, std_dev=2.0, bbw_lookback=120)
                bbw_pct = bb.get("bbw_percentile")
                if bbw_pct is not None:
                    if bbw_pct >= 80:
                        volatility_regime = "HIGH_VOL"
                    elif bbw_pct <= 20:
                        volatility_regime = "LOW_VOL"
                    else:
                        volatility_regime = "NORMAL_VOL"
                else:
                    volatility_regime = "UNKNOWN"
            except Exception:
                volatility_regime = "UNKNOWN"

            result = {
                "state": regime,
                "benchmark": "BTCUSDT",
                "benchmark_price": round(btc_price, 2),
                "hurst": round(hurst, 3),
                "adx_15m": round(adx_15m, 1),
                "adx_4h": round(adx_4h, 1),
                "adx_1d": round(adx_1d, 1),
                "ema_200_proxy": round(ema_200, 2),
                "recommendation": rec,
                "size_multiplier": _size_multiplier(regime),
                "btc_dominance": btc_dom.get("btc_dominance"),
                "eth_dominance": btc_dom.get("eth_dominance"),
                "market_season": btc_dom.get("season", "UNKNOWN"),
                "volatility_regime": volatility_regime,
                "eth_btc_ratio": round(eth_btc_ratio, 6) if eth_btc_ratio else None,
                "fear_greed_index": fear_greed,
                "funding_regime": funding_regime,
            }
            _set_cached(result)

            # Store regime state in Redis for adaptive checkpoints + trailing stops
            try:
                if self._redis:
                    await self._redis.setex("karsa:volatility_regime", 300, volatility_regime)
                    await self._redis.setex("karsa:crypto_regime_state", 300, regime)
            except Exception:
                pass

            try:
                from src.metrics.crypto_metrics import CRYPTO_REGIME, REGIME_SIZE_MULT, DOMINANCE
                _REGIME_ENCODING = {"PURE_DEAD_CHOP": 0, "MEAN_REVERSION": 1, "MACRO_BEAR_MICRO_PULLBACK": 2, "MICRO_BREAKOUT_NO_MACRO": 3, "MACRO_BULL_MICRO_PULLBACK": 4, "FULL_TREND_ALIGNMENT": 5}
                CRYPTO_REGIME.set(_REGIME_ENCODING.get(regime, -1))
                REGIME_SIZE_MULT.set(result["size_multiplier"])
                DOMINANCE.set(result.get("btc_dominance") or 0)
            except Exception:
                pass
                
            try:
                from src.models.database import async_session
                from src.models.tables import CryptoRegimeHistory
                from datetime import datetime
                async with async_session() as session:
                    session.add(CryptoRegimeHistory(
                        timestamp=datetime.utcnow(),
                        regime=regime,
                        hurst=hurst,
                        adx=adx_4h,
                        btc_dominance=result.get("btc_dominance"),
                        size_multiplier=result["size_multiplier"],
                        volatility_regime=volatility_regime,
                    ))
                    await session.commit()
            except Exception as e:
                logger.error("regime_db_persist_failed", error=str(e))

            return result

        except Exception as e:
            logger.error("crypto_regime_failed", error=str(e))
            cached = _regime_cache.get("CRYPTO")
            if cached:
                return cached["data"]
            return self._unknown(str(e))

    def _unknown(self, reason: str) -> dict:
        return {
            "state": "UNKNOWN",
            "benchmark": "BTCUSDT",
            "benchmark_price": "N/A",
            "hurst": "N/A",
            "adx": "N/A",
            "ema_200_proxy": "N/A",
            "recommendation": f"Data unavailable: {reason}",
            "size_multiplier": 1.0,
        }
