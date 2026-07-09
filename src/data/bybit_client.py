"""Karsa Trading System - Bybit Market Data & Execution Client

Wraps pybit (Bybit Unified Trading API) for:
- Market data: tickers, OHLCV, funding rates, open interest, orderbook
- Execution: place/cancel orders, positions, stop-loss/take-profit

Uses Bybit testnet by default. Circuit breaker + caching reuse from MCPClient patterns.
"""

import asyncio
import random
import time
from datetime import datetime, timezone

from pybit.unified_trading import HTTP, WebSocket
from src.config import settings
from src.data.cache import CacheManager
from src.metrics.crypto_metrics import record_bybit_call
from src.utils.logging import get_logger

logger = get_logger("bybit_client")

# Circuit breaker
_CIRCUIT_BREAKER_TTL = 600
_MAX_FAILURES = 5

# Retry config
_MAX_RETRIES = 3
_RETRY_DELAYS = [1, 2, 4]  # exponential backoff in seconds
_JITTER_MAX = 0.5  # max random jitter in seconds

# Bybit V5 error codes
_RETRYABLE_CODES = {10002, 10006, 10016, 30034, 30035, 409}  # timestamp, rate limit, timeout, conflict
_FATAL_CODES = {10001, 10003, 10004, 110001, 110004, 110007}  # params, recv_window, auth, order, balance, risk

# Interval map for Bybit klines
_INTERVAL_MAP = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1D": "D", "1W": "W", "1M": "M",
}


def _safe_float(val, default=0.0) -> float:
    """Convert Bybit response value to float, handling empty strings and None."""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


class BybitClient:
    """Bybit API client for crypto market data and order execution.
    Supports REST for execution and WebSockets for real-time orderbook data.
    """

    def __init__(self, cache: CacheManager):
        self.cache = cache
        self._testnet = settings.BYBIT_TESTNET
        self._http_client = HTTP(
            testnet=self._testnet,
            api_key=settings.BYBIT_API_KEY,
            api_secret=settings.BYBIT_API_SECRET,
        )

        # WebSocket client (lazy init to avoid blocking)
        self._ws_client = None
        self._ob_cache = {}  # {symbol: {"bids": [], "asks": []}}

        # Proxy support (only for Bybit, not Telegram)
        try:
            import os
            proxy = os.environ.get("BYBIT_PROXY")
            if proxy:
                if hasattr(self._http_client, 'client'):
                    self._http_client.client.proxies = {"https": proxy, "http": proxy}
                    self._http_client.client.verify = False
        except Exception:
            pass

        # In-memory cache
        self._cache: dict[tuple, tuple] = {}
        self._cache_ttl = 60  # 1 min for tickers

        # Circuit breaker
        self._failures: dict[str, int] = {}
        self._blocked_until: dict[str, float] = {}

        # Rate limiting
        self._last_request = 0.0
        self._min_interval = 0.1  # 100ms between requests
        self._semaphore = asyncio.Semaphore(5)

    def _is_blocked(self, provider: str) -> bool:
        if provider in self._blocked_until:
            if time.time() < self._blocked_until[provider]:
                return True
            del self._blocked_until[provider]
            self._failures[provider] = 0
        return False

    def _record_failure(self, provider: str):
        self._failures[provider] = self._failures.get(provider, 0) + 1
        if self._failures[provider] >= _MAX_FAILURES:
            self._blocked_until[provider] = time.time() + _CIRCUIT_BREAKER_TTL
            logger.warning("bybit_circuit_breaker", provider=provider)

    def _record_success(self, provider: str):
        self._failures[provider] = 0

    async def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    async def _retry_call(self, func, *args, **kwargs):
        """Retry wrapper with exponential backoff for transient Bybit errors.

        Args:
            func: Synchronous pybit method to call.

        Returns: API response dict.

        Raises: Exception on fatal errors or after max retries.
        """
        last_error = None
        _t0 = time.time()
        for attempt in range(_MAX_RETRIES):
            try:
                async with self._semaphore:
                    await self._throttle()
                    resp = await asyncio.to_thread(func, *args, **kwargs)

                ret_code = resp.get("retCode", 0)

                if ret_code == 0:
                    self._record_success("bybit")
                    record_bybit_call(func.__name__, time.time() - _t0)
                    return resp

                if ret_code in _FATAL_CODES:
                    raise Exception(f"Bybit fatal error ({ret_code}): {resp.get('retMsg')}")

                if ret_code in _RETRYABLE_CODES:
                    last_error = f"Bybit retryable ({ret_code}): {resp.get('retMsg')}"
                    if attempt < _MAX_RETRIES - 1:
                        delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                        jitter = random.uniform(0, _JITTER_MAX)
                        logger.warning("bybit_retry", attempt=attempt + 1, delay=delay + jitter, error=last_error)
                        await asyncio.sleep(delay + jitter)
                        continue

                # Unknown error code — don't retry
                raise Exception(f"Bybit API error ({ret_code}): {resp.get('retMsg')}")

            except Exception as e:
                if "Bybit" in str(e) and ("fatal" in str(e).lower() or "API error" in str(e)):
                    record_bybit_call(func.__name__, time.time() - _t0, error="fatal")
                    raise
                last_error = str(e)
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    jitter = random.uniform(0, _JITTER_MAX)
                    logger.warning("bybit_retry", attempt=attempt + 1, delay=delay + jitter, error=last_error)
                    await asyncio.sleep(delay + jitter)

        self._record_failure("bybit")
        record_bybit_call(func.__name__, time.time() - _t0, error="retry_exhausted")
        raise Exception(f"Bybit max retries exceeded: {last_error}")

    # --- WebSocket Methods ---

    def _init_ws(self):
        """Initialize WebSocket client if not exists."""
        if self._ws_client is None:
            self._ws_client = WebSocket(
                testnet=self._testnet,
                channel_type="linear",
            )
            # Default empty state
            self._ob_cache = {}

    def _handle_ob_message(self, message):
        """Handle incoming orderbook messages (Snapshot or Delta)."""
        data = message.get("data", {})
        symbol = data.get("s")
        if not symbol:
            return

        if symbol not in self._ob_cache or message.get("type") == "snapshot":
            # Initialize with snapshot
            self._ob_cache[symbol] = {
                "bids": {float(price): float(qty) for price, qty in data.get("b", [])},
                "asks": {float(price): float(qty) for price, qty in data.get("a", [])},
                "ts": message.get("ts", 0),
            }
        elif message.get("type") == "delta":
            # Apply delta updates to existing snapshot
            ob = self._ob_cache[symbol]
            for price, qty in data.get("b", []):
                p, q = float(price), float(qty)
                if q == 0 and p in ob["bids"]:
                    del ob["bids"][p]
                elif q > 0:
                    ob["bids"][p] = q

            for price, qty in data.get("a", []):
                p, q = float(price), float(qty)
                if q == 0 and p in ob["asks"]:
                    del ob["asks"][p]
                elif q > 0:
                    ob["asks"][p] = q
            ob["ts"] = message.get("ts", 0)

    async def get_orderbook_imbalance(self, symbol: str, depth: int = 50) -> float:
        """Calculate Bid/Ask volume imbalance using WebSocket orderbook feed.

        Subscribes to the orderbook feed if not already subscribed.
        Returns float between -1.0 (100% Ask) and 1.0 (100% Bid).
        """
        self._init_ws()

        if symbol not in self._ob_cache:
            try:
                # Subscribe and wait briefly for the first snapshot
                self._ws_client.orderbook_stream(
                    depth=depth,
                    symbol=symbol,
                    callback=self._handle_ob_message
                )
                await asyncio.sleep(0.5)  # Wait for ws to connect and push snapshot
            except Exception as e:
                logger.error("ws_subscribe_failed", symbol=symbol, error=str(e))
                return 0.0

        ob = self._ob_cache.get(symbol)
        if not ob or not ob.get("bids") or not ob.get("asks"):
            return 0.0

        # Calculate imbalance
        bids = sorted(ob["bids"].items(), key=lambda x: x[0], reverse=True)[:depth]
        asks = sorted(ob["asks"].items(), key=lambda x: x[0])[:depth]

        bid_vol = sum(p * q for p, q in bids)
        ask_vol = sum(p * q for p, q in asks)
        target = bid_vol + ask_vol

        if target == 0:
            return 0.0

        return (bid_vol - ask_vol) / target

    # --- Data Methods ---

    async def get_top_movers(self, top_n: int = 20, min_volume_usd: float = 10_000_000) -> list[dict]:
        """Fetch top USDT perpetuals by 24h turnover from Bybit.

        Returns list of {symbol, volume_usd, change_pct, price} sorted by volume desc.
        Cached for 5 minutes. Used by dynamic universe discovery.
        """
        cache_key = ("top_movers", top_n)
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[1] < 300:  # 5min cache
            return cached[0]

        try:
            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.get_tickers,
                    category="linear",
                )

            if resp.get("retCode") != 0:
                logger.error("bybit_top_movers_failed", error=resp.get("retMsg"))
                return []

            result_list = resp.get("result", {}).get("list", [])

            # Filter USDT perps only, sort by turnover (volume * price)
            movers = []
            for data in result_list:
                symbol = data.get("symbol", "")
                if not symbol.endswith("USDT"):
                    continue
                turnover = _safe_float(data.get("turnover24h", 0))
                if turnover < min_volume_usd:
                    continue
                movers.append({
                    "symbol": symbol,
                    "volume_usd": turnover,
                    "change_pct": _safe_float(data.get("price24hPcnt", 0)) * 100,
                    "price": _safe_float(data.get("lastPrice", 0)),
                    "funding_rate": _safe_float(data.get("fundingRate", 0)),
                })

            movers.sort(key=lambda x: x["volume_usd"], reverse=True)
            result = movers[:top_n]
            self._cache[cache_key] = (result, time.time())
            return result

        except Exception as e:
            logger.error("bybit_top_movers_failed", error=str(e))
            return []

    async def get_all_perps(self, min_volume_usd: float = 1_000_000) -> list[dict]:
        """Fetch all USDT perpetuals with data needed for universe scoring.

        Returns list of {symbol, volume_24h_usd, price_change_pct, turnover_ratio, price, funding_rate, open_interest}.
        Cached for 5 minutes. Used by UniverseEngine for dynamic universe generation.
        """
        cache_key = ("all_perps", min_volume_usd)
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[1] < 300:
            return cached[0]

        try:
            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.get_tickers,
                    category="linear",
                )

            if resp.get("retCode") != 0:
                logger.error("bybit_all_perps_failed", error=resp.get("retMsg"))
                return []

            result_list = resp.get("result", {}).get("list", [])
            perps = []
            for data in result_list:
                symbol = data.get("symbol", "")
                if not symbol.endswith("USDT"):
                    continue
                turnover = _safe_float(data.get("turnover24h", 0))
                if turnover < min_volume_usd:
                    continue
                oi = _safe_float(data.get("openInterest", 0))
                perps.append({
                    "symbol": symbol,
                    "volume_24h_usd": turnover,
                    "price_change_pct": _safe_float(data.get("price24hPcnt", 0)) * 100,
                    "turnover_ratio": turnover / (oi * _safe_float(data.get("lastPrice", 1))) if oi > 0 else 0,
                    "price": _safe_float(data.get("lastPrice", 0)),
                    "funding_rate": _safe_float(data.get("fundingRate", 0)),
                    "open_interest": oi,
                })

            self._cache[cache_key] = (perps, time.time())
            return perps

        except Exception as e:
            logger.error("bybit_all_perps_failed", error=str(e))
            return []

    async def get_ticker(self, symbol: str) -> dict:
        """Get real-time ticker for a Bybit perpetual symbol (e.g. BTCUSDT)."""
        cache_key = ("ticker", symbol)
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[1] < self._cache_ttl:
            return cached[0]

        if self._is_blocked("bybit"):
            return {"symbol": symbol, "price": 0, "error": "circuit_breaker_active"}

        try:
            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.get_tickers,
                    category="linear",
                    symbol=symbol,
                )

            if resp.get("retCode") != 0:
                raise Exception(f"Bybit API error: {resp.get('retMsg')}")

            result_list = resp.get("result", {}).get("list", [])
            if not result_list:
                return {"symbol": symbol, "price": 0, "error": "no_data"}

            data = result_list[0]
            price = _safe_float(data.get("lastPrice", 0))
            tick = {
                "symbol": symbol,
                "ticker": symbol,
                "market": "CRYPTO",
                "price": price,
                "change": round(price - _safe_float(data.get("prevPrice24h", price)), 4),
                "change_pct": _safe_float(data.get("price24hPcnt", 0)) * 100,
                "volume": _safe_float(data.get("volume24h", 0)),
                "open": _safe_float(data.get("prevPrice24h", 0)),
                "high": _safe_float(data.get("highPrice24h", 0)),
                "low": _safe_float(data.get("lowPrice24h", 0)),
                "bid": _safe_float(data.get("bid1Price", 0)),
                "ask": _safe_float(data.get("ask1Price", 0)),
                "funding_rate": _safe_float(data.get("fundingRate", 0)),
                "open_interest": _safe_float(data.get("openInterest", 0)),
                "index_price": _safe_float(data.get("indexPrice", 0)),
                "mark_price": _safe_float(data.get("markPrice", 0)),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "bybit",
            }
            self._cache[cache_key] = (tick, time.time())
            self._record_success("bybit")

            # Cache in Redis too
            await self.cache.set_quote(symbol, "CRYPTO", tick)
            return tick

        except Exception as e:
            self._record_failure("bybit")
            logger.error("bybit_ticker_failed", symbol=symbol, error=str(e))
            return {"symbol": symbol, "price": 0, "error": str(e)}

    async def get_ohlcv(self, symbol: str, interval: str = "1D", limit: int = 200) -> list[dict]:
        """Get OHLCV klines for a Bybit perpetual symbol."""
        cache_key = ("ohlcv", symbol, interval, limit)
        cached = self._cache.get(cache_key)
        
        # Tiered TTL based on interval
        ttl = 300
        if interval in ("15", "15m"):
            ttl = 900  # 15 minutes
        elif interval in ("240", "4h", "4H"):
            ttl = 14400  # 4 hours
        elif interval in ("D", "1D", "1d"):
            ttl = 86400  # 24 hours

        if cached and time.time() - cached[1] < ttl:
            return cached[0]

        try:
            bybit_interval = _INTERVAL_MAP.get(interval, "D")
            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.get_kline,
                    category="linear",
                    symbol=symbol,
                    interval=bybit_interval,
                    limit=limit,
                )

            if resp.get("retCode") != 0:
                raise Exception(f"Bybit API error: {resp.get('retMsg')}")

            klines = []
            for row in resp.get("result", {}).get("list", []):
                # Bybit returns [startTime, open, high, low, close, volume, turnover]
                klines.append({
                    "timestamp": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).isoformat(),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                })

            self._cache[cache_key] = (klines, time.time())
            await self.cache.set_ohlcv(symbol, "CRYPTO", interval, klines)
            return klines

        except Exception as e:
            logger.error("bybit_ohlcv_failed", symbol=symbol, error=str(e))
            return []

    async def get_funding_rate(self, symbol: str) -> dict:
        """Get current funding rate for a perpetual symbol."""
        try:
            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.get_funding_rate_history,
                    category="linear",
                    symbol=symbol,
                    limit=1,
                )

            if resp.get("retCode") != 0:
                raise Exception(f"Bybit API error: {resp.get('retMsg')}")

            result_list = resp.get("result", {}).get("list", [])
            if not result_list:
                return {"symbol": symbol, "funding_rate": 0, "error": "no_data"}

            data = result_list[0]
            return {
                "symbol": symbol,
                "funding_rate": float(data.get("fundingRate", 0)),
                "funding_time": data.get("fundingRateTimestamp"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error("bybit_funding_failed", symbol=symbol, error=str(e))
            return {"symbol": symbol, "funding_rate": 0, "error": str(e)}

    async def get_funding_history(self, symbol: str, limit: int = 200, start_time: int | None = None) -> list[dict]:
        """Get historical funding rate payments for a symbol.

        Returns: list of {funding_rate, funding_fee, position_size, side, funded_at}
        """
        try:
            params = {"category": "linear", "symbol": symbol, "limit": limit}
            if start_time:
                params["startTime"] = start_time

            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.get_funding_history,
                    **params,
                )

            if resp.get("retCode") != 0:
                raise Exception(f"Bybit API error: {resp.get('retMsg')}")

            records = []
            for item in resp.get("result", {}).get("list", []):
                records.append({
                    "symbol": symbol,
                    "funding_rate": _safe_float(item.get("fundingRate", 0)),
                    "funding_fee": _safe_float(item.get("fundingFee", 0)),
                    "position_size": _safe_float(item.get("size", 0)),
                    "side": item.get("side", ""),
                    "funded_at": datetime.fromtimestamp(
                        int(item.get("fundingRateTimestamp", 0)) / 1000, tz=timezone.utc
                    ).isoformat(),
                })

            return records

        except Exception as e:
            logger.error("bybit_funding_history_failed", symbol=symbol, error=str(e))
            return []

    async def get_open_interest(self, symbol: str) -> dict:
        """Get open interest for a perpetual symbol."""
        try:
            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.get_open_interest,
                    category="linear",
                    symbol=symbol,
                    intervalTime="1d",
                    limit=1,
                )

            if resp.get("retCode") != 0:
                raise Exception(f"Bybit API error: {resp.get('retMsg')}")

            result_list = resp.get("result", {}).get("list", [])
            if not result_list:
                return {"symbol": symbol, "open_interest": 0, "error": "no_data"}

            data = result_list[0]
            return {
                "symbol": symbol,
                "open_interest": float(data.get("openInterest", 0)),
                "timestamp": data.get("timestamp"),
            }

        except Exception as e:
            logger.error("bybit_oi_failed", symbol=symbol, error=str(e))
            return {"symbol": symbol, "open_interest": 0, "error": str(e)}

    async def get_orderbook(self, symbol: str, limit: int = 25) -> dict:
        """Get L2 orderbook for a perpetual symbol."""
        try:
            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.get_orderbook,
                    category="linear",
                    symbol=symbol,
                    limit=limit,
                )

            if resp.get("retCode") != 0:
                raise Exception(f"Bybit API error: {resp.get('retMsg')}")

            result = resp.get("result", {})
            return {
                "symbol": symbol,
                "bids": [[float(p), float(q)] for p, q in result.get("b", [])],
                "asks": [[float(p), float(q)] for p, q in result.get("a", [])],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error("bybit_orderbook_failed", symbol=symbol, error=str(e))
            return {"symbol": symbol, "bids": [], "asks": [], "error": str(e)}

    # --- Execution Methods ---

    async def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "Limit",
        price: float | None = None,
        time_in_force: str = "PostOnly",
        reduce_only: bool = False,
    ) -> dict:
        """Place an order on Bybit.

        Args:
            symbol: e.g. "BTCUSDT"
            side: "Buy" or "Sell"
            qty: order quantity (in base currency)
            order_type: "Limit" or "Market"
            price: required for Limit orders
            time_in_force: "PostOnly" for maker, "GTC" for taker
            reduce_only: True for closing positions

        Returns:
            {order_id, status, ...} or {error: ...}
        """
        if not settings.BYBIT_API_KEY:
            return {"error": "Bybit API key not configured"}

        try:
            params = {
                "category": "linear",
                "symbol": symbol,
                "side": side,
                "orderType": order_type,
                "qty": str(qty),
                "reduceOnly": reduce_only,
            }
            if order_type == "Limit" and price:
                params["price"] = str(price)
                params["timeInForce"] = time_in_force
            else:
                params["timeInForce"] = "IOC"

            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.place_order,
                    **params,
                )

            if resp.get("retCode") != 0:
                return {"error": f"Bybit order error: {resp.get('retMsg')}", "retCode": resp.get("retCode")}

            result = resp.get("result", {})
            logger.info("bybit_order_placed", symbol=symbol, side=side, order_id=result.get("orderId"))
            return {
                "order_id": result.get("orderId"),
                "status": "placed",
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
                "order_type": order_type,
            }

        except Exception as e:
            logger.error("bybit_order_failed", symbol=symbol, error=str(e))
            return {"error": str(e)}

    async def get_open_orders(self, symbol: str | None = None, category: str = "linear") -> list[dict]:
        """Get open (active) orders. If symbol is None, returns all open orders."""
        try:
            params = {"category": category, "settleCoin": "USDT"}
            if symbol:
                params["symbol"] = symbol

            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.get_open_orders,
                    **params,
                )

            if resp.get("retCode") != 0:
                return []

            return resp.get("result", {}).get("list", [])

        except Exception as e:
            logger.error("bybit_open_orders_failed", symbol=symbol, error=str(e))
            return []

    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Cancel an open order."""
        try:
            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.cancel_order,
                    category="linear",
                    symbol=symbol,
                    orderId=order_id,
                )

            if resp.get("retCode") != 0:
                return {"error": f"Cancel error: {resp.get('retMsg')}"}

            return {"order_id": order_id, "status": "cancelled"}

        except Exception as e:
            logger.error("bybit_cancel_failed", symbol=symbol, order_id=order_id, error=str(e))
            return {"error": str(e)}

    async def get_positions(self, symbol: str | None = None) -> list[dict]:
        """Get open positions. If symbol is None, returns all positions."""
        try:
            params = {"category": "linear", "settleCoin": "USDT"}
            if symbol:
                params["symbol"] = symbol

            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.get_positions,
                    **params,
                )

            if resp.get("retCode") != 0:
                return []

            positions = []
            for p in resp.get("result", {}).get("list", []):
                size = _safe_float(p.get("size", 0))
                if size == 0:
                    continue
                positions.append({
                    "symbol": p.get("symbol"),
                    "ticker": p.get("symbol"),
                    "market": "CRYPTO",
                    "side": p.get("side"),  # "Buy" or "Sell"
                    "size": size,
                    "entry_price": _safe_float(p.get("avgPrice", 0)),
                    "current_price": _safe_float(p.get("markPrice", 0)),
                    "unrealized_pnl": _safe_float(p.get("unrealisedPnl", 0)),
                    "leverage": _safe_float(p.get("leverage", 1)),
                    "margin": _safe_float(p.get("positionIM", 0)),
                    "liquidation_price": _safe_float(p.get("liqPrice", 0)) or None,
                    "stop_loss": _safe_float(p.get("stopLoss", 0)) or None,
                    "take_profit": _safe_float(p.get("takeProfit", 0)) or None,
                    "funding_fee": _safe_float(p.get("curRealisedPnl", 0)),
                    "position_idx": p.get("positionIdx", 0),
                    "opened_at": p.get("createdTime"),
                })

            return positions

        except Exception as e:
            logger.error("bybit_positions_failed", error=str(e))
            return []

    async def set_stop_loss(self, symbol: str, stop_price: float, side: str) -> dict:
        """Set stop-loss on an existing position."""
        try:
            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.set_trading_stop,
                    category="linear",
                    symbol=symbol,
                    stopLoss=str(stop_price),
                    tpslMode="Full",
                    positionIdx=0,
                )

            if resp.get("retCode") != 0:
                return {"error": f"SL error: {resp.get('retMsg')}"}

            logger.info("bybit_sl_set", symbol=symbol, stop_price=stop_price)
            return {"symbol": symbol, "stop_loss": stop_price, "status": "set"}

        except Exception as e:
            logger.error("bybit_sl_failed", symbol=symbol, error=str(e))
            return {"error": str(e)}

    async def set_take_profit(self, symbol: str, tp_price: float, side: str) -> dict:
        """Set take-profit on an existing position."""
        try:
            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.set_trading_stop,
                    category="linear",
                    symbol=symbol,
                    takeProfit=str(tp_price),
                    tpslMode="Full",
                    positionIdx=0,
                )

            if resp.get("retCode") != 0:
                return {"error": f"TP error: {resp.get('retMsg')}"}

            logger.info("bybit_tp_set", symbol=symbol, tp_price=tp_price)
            return {"symbol": symbol, "take_profit": tp_price, "status": "set"}

        except Exception as e:
            logger.error("bybit_tp_failed", symbol=symbol, error=str(e))
            return {"error": str(e)}

    async def get_order_status(self, symbol: str, order_id: str) -> dict:
        """Query order status by ID. Used by SOR to verify fills."""
        try:
            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.get_order_history,
                    category="linear",
                    orderId=order_id,
                )

            if resp.get("retCode") != 0:
                return {"error": f"Order query error: {resp.get('retMsg')}"}

            result_list = resp.get("result", {}).get("list", [])
            if not result_list:
                return {"status": "unknown", "filled_qty": 0, "avg_price": 0}

            order = result_list[0]
            return {
                "order_id": order_id,
                "status": order.get("orderStatus"),  # Filled, New, Cancelled, etc.
                "filled_qty": _safe_float(order.get("cumExecQty", 0)),
                "avg_price": _safe_float(order.get("avgPrice", 0)),
                "leaves_qty": _safe_float(order.get("leavesQty", 0)),
            }

        except Exception as e:
            logger.error("bybit_order_status_failed", symbol=symbol, order_id=order_id, error=str(e))
            return {"error": str(e)}

    async def get_closed_pnl(self, symbol: str | None = None, limit: int = 20) -> list[dict]:
        """Get closed PnL history from Bybit. Used for accurate PnL recording on phantom closes."""
        try:
            params = {"category": "linear", "limit": limit}
            if symbol:
                params["symbol"] = symbol

            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.get_closed_pnl,
                    **params,
                )

            if resp.get("retCode") != 0:
                logger.warning("bybit_closed_pnl_failed", retMsg=resp.get("retMsg"))
                return []

            results = []
            for item in resp.get("result", {}).get("list", []):
                results.append({
                    "symbol": item.get("symbol"),
                    "side": item.get("side"),
                    "qty": _safe_float(item.get("qty", 0)),
                    "entry_price": _safe_float(item.get("avgEntryPrice", 0)),
                    "exit_price": _safe_float(item.get("avgExitPrice", 0)),
                    "closed_pnl": _safe_float(item.get("closedPnl", 0)),
                    "fill_count": item.get("fillCount", 0),
                    "leverage": _safe_float(item.get("leverage", 1)),
                    "created_time": item.get("createdTime"),
                    "updated_time": item.get("updatedTime"),
                })
            return results

        except Exception as e:
            logger.error("bybit_closed_pnl_failed", symbol=symbol, error=str(e))
            return []

    async def get_wallet_balance(self, coin: str | None = None) -> dict:
        """Get wallet balance. coin=None fetches all coins; pass 'USDT' for USDT-only."""
        try:
            async with self._semaphore:
                await self._throttle()
                params = {"accountType": "UNIFIED"}
                if coin:
                    params["coin"] = coin
                resp = await asyncio.to_thread(
                    self._http_client.get_wallet_balance,
                    **params,
                )

            if resp.get("retCode") != 0:
                return {"error": resp.get("retMsg")}

            result = resp.get("result", {}).get("list", [])
            if not result:
                return {"balance": 0, "available": 0, "coins": []}

            account = result[0]
            coins = account.get("coin", [])
            usdt = next((c for c in coins if c.get("coin") == "USDT"), {})

            # Build full coin list (skip zero-equity coins)
            all_coins = []
            for c in coins:
                equity = _safe_float(c.get("equity", 0))
                if equity > 0:
                    all_coins.append({
                        "coin": c.get("coin"),
                        "equity": equity,
                        "available": _safe_float(c.get("availableToWithdraw", 0)),
                        "used_margin": _safe_float(c.get("usedMargin", 0)),
                        "unrealized_pnl": _safe_float(c.get("unrealisedPnl", 0)),
                    })

            return {
                "balance": _safe_float(usdt.get("equity", 0)),
                "available": _safe_float(usdt.get("availableToWithdraw", 0)),
                "used_margin": _safe_float(usdt.get("usedMargin", 0)),
                "unrealized_pnl": _safe_float(usdt.get("unrealisedPnl", 0)),
                "coins": all_coins,
            }

        except Exception as e:
            logger.error("bybit_wallet_failed", error=str(e))
            return {"balance": 0, "available": 0, "coins": [], "error": str(e)}

    async def validate_api_key(self) -> dict:
        """Validate API key by querying user info."""
        try:
            async with self._semaphore:
                await self._throttle()
                resp = await asyncio.to_thread(
                    self._http_client.get_api_key_information,
                )

            if resp.get("retCode") != 0:
                return {"valid": False, "error": resp.get("retMsg", "Unknown error")}

            result = resp.get("result", {})
            return {
                "valid": True,
                "uid": result.get("uid", "?"),
                "permissions": result.get("permissions", {}),
                "type": result.get("type", 0),
            }

        except Exception as e:
            logger.error("api_key_validation_failed", error=str(e))
            return {"valid": False, "error": str(e)[:100]}

    async def close(self):
        """Cleanup (pybit HTTP client doesn't have explicit close)."""
        pass
