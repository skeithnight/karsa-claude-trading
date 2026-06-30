"""Karsa Trading System - Market Data Client

Data sources (tiered fallback):
- Tier 1: TradingView (primary)
- Tier 2: Massive API (Polygon-compatible)
- Tier 3: Finnhub.io (secondary fallback)

Features:
- Redis caching with TTL (60s quotes, 300s bars)
- Circuit breaker (block provider for 10min after 3 failures)
- Semaphore(1) to prevent self-DDoS
"""

import asyncio
import json
import time
from datetime import datetime, timezone

import httpx

from src.data.cache import CacheManager
from src.utils.logging import get_logger

logger = get_logger("mcp_client")

_ta_handler = None

# API Configuration (from .env via settings)
from src.config import settings
MASSIVE_API_KEY = settings.MASSIVE_API_KEY
MASSIVE_BASE_URL = settings.MASSIVE_BASE_URL
FINNHUB_API_KEY = settings.FINNHUB_API_KEY
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

# Circuit breaker config
CIRCUIT_BREAKER_TTL = 600  # 10 minutes
MAX_FAILURES = 3

SCREENER_MAP = {"IDX": "indonesia", "US": "america", "ETF": "america"}
EXCHANGE_MAP = {"IDX": "IDX", "US": "NASDAQ", "ETF": "AMEX"}


def _ensure_imports():
    global _ta_handler
    if _ta_handler is None:
        from tradingview_ta import TA_Handler, Interval
        _ta_handler = (TA_Handler, Interval)


class MCPClient:
    """Market data client with tiered fallback and circuit breaker."""

    def __init__(self, cache: CacheManager):
        self.cache = cache
        # In-memory cache
        self._ta_cache: dict[tuple, tuple] = {}
        self._ta_cache_ttl = 300  # 5 minutes
        self._last_request_time = 0
        self._min_request_interval = 1.0

        # Semaphore to prevent concurrent API calls
        self._semaphore = asyncio.Semaphore(3)

        # Circuit breaker state
        self._failures: dict[str, int] = {}  # provider -> failure count
        self._blocked_until: dict[str, float] = {}  # provider -> unblock timestamp

        # HTTP client for fallback APIs
        self._http_client = httpx.AsyncClient(timeout=10.0)

    async def close(self):
        await self._http_client.aclose()

    def _is_provider_blocked(self, provider: str) -> bool:
        """Check if a provider is blocked by circuit breaker."""
        if provider in self._blocked_until:
            if time.time() < self._blocked_until[provider]:
                return True
            else:
                # Unblock after TTL
                del self._blocked_until[provider]
                self._failures[provider] = 0
        return False

    def _record_failure(self, provider: str):
        """Record a failure and block provider if threshold exceeded."""
        self._failures[provider] = self._failures.get(provider, 0) + 1
        if self._failures[provider] >= MAX_FAILURES:
            self._blocked_until[provider] = time.time() + CIRCUIT_BREAKER_TTL
            logger.warning("circuit_breaker_activated", provider=provider, ttl=CIRCUIT_BREAKER_TTL)

    def _record_success(self, provider: str):
        """Record a success and reset failure count."""
        self._failures[provider] = 0

    def _get_ta(self, ticker: str, market: str, timeframe: str = "1D"):
        """TradingView TA handler with caching."""
        import time as time_mod
        _ensure_imports()
        TA_Handler, Interval = _ta_handler

        cache_key = (ticker, market, timeframe)
        if cache_key in self._ta_cache:
            cached_analysis, cached_ts = self._ta_cache[cache_key]
            if time_mod.time() - cached_ts < self._ta_cache_ttl:
                return cached_analysis

        elapsed = time_mod.time() - self._last_request_time
        if elapsed < self._min_request_interval:
            time_mod.sleep(self._min_request_interval - elapsed)

        interval_map = {
            "1m": Interval.INTERVAL_1_MINUTE, "5m": Interval.INTERVAL_5_MINUTES,
            "15m": Interval.INTERVAL_15_MINUTES, "1h": Interval.INTERVAL_1_HOUR,
            "4h": Interval.INTERVAL_4_HOURS, "1D": Interval.INTERVAL_1_DAY,
            "1W": Interval.INTERVAL_1_WEEK,
        }
        # Handle index symbols that need special treatment
        if ticker in ("IHSG", "IHSG.JK"):
            ticker = "COMPOSITE"
        elif ticker in ("VIX", "VIXY"):
            ticker = "VIX"
        screener = SCREENER_MAP.get(market, "america")
        exchanges = [EXCHANGE_MAP.get(market, "NASDAQ")]
        if market in ("US", "ETF"):
            exchanges = ["NASDAQ", "NYSE", "AMEX", "CBOE"]

        last_err = None
        for ex in exchanges:
            try:
                self._last_request_time = time_mod.time()
                result = TA_Handler(
                    symbol=ticker, screener=screener, exchange=ex,
                    interval=interval_map.get(timeframe, Interval.INTERVAL_1_DAY),
                ).get_analysis()
                self._ta_cache[cache_key] = (result, time_mod.time())
                return result
            except Exception as e:
                last_err = e
                if "429" in str(e):
                    time_mod.sleep(2.0)
                continue
        raise last_err or Exception("No exchange found")

    async def _fetch_massive_quote(self, ticker: str, market: str) -> dict:
        """Fetch quote from Massive API (Tier 2)."""
        url = f"{MASSIVE_BASE_URL}/v2/last/trade/{ticker}"
        response = await self._http_client.get(url, headers={"Authorization": f"Bearer {MASSIVE_API_KEY}"})
        response.raise_for_status()
        data = response.json()

        price = data.get("results", {}).get("p", 0)
        return {
            "ticker": ticker, "market": market, "price": float(price),
            "timestamp": datetime.now(timezone.utc).isoformat(), "source": "massive"
        }

    async def _fetch_finnhub_quote(self, ticker: str, market: str = "US") -> dict:
        """Fetch quote from Finnhub (Tier 3)."""
        if not FINNHUB_API_KEY:
            raise Exception("Finnhub API key not configured")

        url = f"{FINNHUB_BASE_URL}/quote"
        response = await self._http_client.get(url, params={"symbol": ticker}, headers={"X-Finnhub-Token": FINNHUB_API_KEY})
        response.raise_for_status()
        data = response.json()

        price = data.get("c", 0)  # Current price
        return {
            "ticker": ticker, "market": market, "price": float(price),
            "timestamp": datetime.now(timezone.utc).isoformat(), "source": "finnhub"
        }

    async def _fetch_with_fallback(self, ticker: str, market: str) -> dict:
        """Fetch quote with tiered fallback and circuit breaker."""
        async with self._semaphore:
            # Tier 1: TradingView
            if not self._is_provider_blocked("tradingview"):
                try:
                    analysis = await asyncio.to_thread(self._get_ta, ticker, market, "1D")
                    ind = analysis.indicators
                    price = float(ind.get("close", 0))
                    if price > 0:
                        self._record_success("tradingview")
                        return {
                            "ticker": ticker, "market": market, "price": price,
                            "change": round(price - float(ind.get("open", price)), 2),
                            "change_pct": (
                                round((price - float(ind.get("open", price))) / float(ind.get("open", price)) * 100, 2)
                                if float(ind.get("open", 0)) not in (0, 0.0) else 0
                            ),
                            "volume": int(ind.get("volume", 0) or 0),
                            "open": float(ind.get("open", 0)), "high": float(ind.get("high", 0)),
                            "low": float(ind.get("low", 0)),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                except Exception as e:
                    self._record_failure("tradingview")
                    logger.warning("tradingview_failed", ticker=ticker, error=str(e))

            # Tier 2: Massive API
            if not self._is_provider_blocked("massive"):
                try:
                    result = await self._fetch_massive_quote(ticker, market)
                    self._record_success("massive")
                    return result
                except Exception as e:
                    self._record_failure("massive")
                    logger.warning("massive_failed", ticker=ticker, error=str(e))

            # Tier 3: Finnhub (US only)
            if market in ("US", "ETF") and not self._is_provider_blocked("finnhub"):
                try:
                    result = await self._fetch_finnhub_quote(ticker, market)
                    self._record_success("finnhub")
                    return result
                except Exception as e:
                    self._record_failure("finnhub")
                    logger.warning("finnhub_failed", ticker=ticker, error=str(e))

            # All providers failed
            return {"ticker": ticker, "market": market, "price": 0, "error": "All data providers failed"}

    async def get_quote(self, ticker: str, market: str) -> dict:
        """Get real-time quote with caching and fallback."""
        # Check Redis cache first
        cached = await self.cache.get_quote(ticker, market)
        if cached:
            return cached

        # Fetch with fallback
        quote = await self._fetch_with_fallback(ticker, market)

        # Cache successful results
        if not quote.get("error"):
            await self.cache.set_quote(ticker, market, quote)

        return quote

    async def get_ohlcv(self, ticker: str, market: str, timeframe: str = "1D", limit: int = 100) -> list[dict]:
        """Get OHLCV data with caching."""
        cached = await self.cache.get_ohlcv(ticker, market, timeframe)
        if cached:
            return cached

        try:
            analysis = await asyncio.to_thread(self._get_ta, ticker, market, timeframe)
            ind = analysis.indicators
            candle = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "open": float(ind.get("open", 0)), "high": float(ind.get("high", 0)),
                "low": float(ind.get("low", 0)), "close": float(ind.get("close", 0)),
                "volume": int(ind.get("volume", 0) or 0),
            }
            await self.cache.set_ohlcv(ticker, market, timeframe, [candle])
            return [candle]
        except Exception as e:
            logger.error("get_ohlcv_failed", ticker=ticker, market=market, error=str(e))
            return []

    async def get_technical(self, ticker: str, market: str, indicator: str, params: dict | None = None) -> dict:
        """Get technical indicators with fallback."""
        async with self._semaphore:
            # Tier 1: TradingView
            if not self._is_provider_blocked("tradingview"):
                try:
                    analysis = await asyncio.to_thread(self._get_ta, ticker, market, "1D")
                    self._record_success("tradingview")
                    return {"indicators": analysis.indicators, "ticker": ticker, "market": market}
                except Exception as e:
                    if "429" in str(e):
                        self._record_failure("tradingview")
                    logger.warning("tradingview_technical_failed", ticker=ticker, error=str(e))

            # Return empty indicators if all providers fail
            return {"indicators": {}, "error": "Technical data unavailable"}

    async def get_rsi(self, ticker: str, market: str, period: int = 14) -> float:
        r = await self.get_technical(ticker, market, "RSI")
        return float(r.get("indicators", {}).get("RSI", 50))

    async def get_bollinger(self, ticker: str, market: str, period: int = 20, std_dev: float = 2.0) -> dict:
        r = await self.get_technical(ticker, market, "BB")
        ind = r.get("indicators", {})
        return {"upper": float(ind.get("BB.upper", 0)), "middle": float(ind.get("BB.middle", 0)), "lower": float(ind.get("BB.lower", 0))}

    async def get_ema(self, ticker: str, market: str, period: int) -> float:
        r = await self.get_technical(ticker, market, "EMA")
        return float(r.get("indicators", {}).get(f"EMA{period}", 0))
