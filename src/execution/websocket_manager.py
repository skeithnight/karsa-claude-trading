"""Karsa Trading System — WebSocket Price Streaming Manager

Maintains persistent Bybit WS connections for open positions.
Updates Redis karsa:realtime:price:{ticker} on each tick.
Auto-subscribes/unsubscribes as positions open/close.

Flow:
  main.py starts WebSocketManager.run() as background task →
  Every 30s: sync subscriptions with current open positions →
  On tick: update Redis price cache instantly.
"""

import asyncio
import json
import random
import time

from src.config import settings
from src.metrics.crypto_metrics import update_ws_health_tick, update_ws_reconnect
from src.utils.logging import get_logger

logger = get_logger("websocket_manager")

REDIS_PRICE_PREFIX = "karsa:realtime:price"
REDIS_TICK_CHANNEL = "karsa:events:price_tick"
SYNC_INTERVAL_SEC = 30
MAX_RETRIES = 10
BASE_DELAY = 2
STALE_THRESHOLD_SEC = 30

class WebSocketManager:
    """Real-time price streaming from Bybit for open positions only."""

    def __init__(self, redis_client, bybit_client):
        self._redis = redis_client
        self._bybit = bybit_client
        self._subscribed: set[str] = set()
        self._last_tick_time: dict[str, float] = {}
        self._ws = None
        self._running = False

    async def run(self) -> None:
        """Main loop: sync subscriptions with exponential backoff on failure."""
        self._running = True
        attempt = 0
        logger.info("ws_manager_started")

        while self._running:
            try:
                await self._sync_subscriptions()
                self._check_stale_ticks()
                attempt = 0  # success resets backoff
                try:
                    from src.metrics.crypto_metrics import record_ws_connected
                    record_ws_connected(True)
                except Exception:
                    pass
            except Exception as e:
                attempt += 1
                if attempt > MAX_RETRIES:
                    logger.critical("ws_max_retries_exceeded", attempts=attempt,
                                    message="Positions UNPROTECTED — WS reconnect failed")
                    break
                delay = min(BASE_DELAY * (2 ** attempt), 60) + random.uniform(0, 1)
                logger.warning("ws_sync_failed", error=str(e), attempt=attempt,
                               retry_in=f"{delay:.1f}s")
                try:
                    from src.metrics.crypto_metrics import record_ws_connected
                    record_ws_connected(False)
                except Exception:
                    pass
                # Force WS recreation on next iteration
                if self._ws:
                    try:
                        self._ws.exit()
                    except Exception:
                        pass
                    self._ws = None
                    self._subscribed.clear()
                    self._last_tick_time.clear()
                await asyncio.sleep(delay)
                continue
            await asyncio.sleep(SYNC_INTERVAL_SEC)

    async def stop(self) -> None:
        """Gracefully stop the manager."""
        self._running = False
        if self._ws:
            try:
                self._ws.exit()
            except Exception:
                pass
        logger.info("ws_manager_stopped")

    def _check_stale_ticks(self) -> None:
        """Raise if any subscribed ticker hasn't ticked recently."""
        now = time.time()
        for ticker in list(self._subscribed):
            last = self._last_tick_time.get(ticker, 0)
            if last and (now - last) > STALE_THRESHOLD_SEC:
                logger.warning("ws_ticker_stale", ticker=ticker,
                               silence=f"{now - last:.0f}s")
                raise ConnectionError(f"Ticker {ticker} stale for {now - last:.0f}s")

    async def _sync_subscriptions(self) -> None:
        """Sync WS subscriptions with current open positions."""
        try:
            positions = await self._bybit.get_positions()
            if positions is None:
                return

            open_tickers = set()
            for p in positions:
                sym = p.get("symbol", "")
                size = float(p.get("size", 0) or 0)
                if sym and size > 0:
                    open_tickers.add(sym)

            # Also include pending limit orders
            try:
                orders = await self._bybit.get_open_orders()
                if orders:
                    for o in orders:
                        sym = o.get("symbol", "")
                        if sym:
                            open_tickers.add(sym)
            except Exception:
                pass

            # Subscribe to new tickers
            new_subs = open_tickers - self._subscribed
            for ticker in new_subs:
                await self._subscribe_ticker(ticker)

            # Unsubscribe removed tickers
            removed = self._subscribed - open_tickers
            for ticker in removed:
                await self._unsubscribe_ticker(ticker)

        except Exception as e:
            logger.error("ws_position_sync_failed", error=str(e))

    async def _subscribe_ticker(self, ticker: str) -> None:
        """Subscribe to a ticker's trade stream."""
        try:
            if self._ws is None:
                from pybit.unified_trading import WebSocket
                self._ws = WebSocket(
                    testnet=settings.BYBIT_TESTNET,
                    channel_type="linear",
                )
                update_ws_reconnect()

            def on_tick(message):
                # ponytail: pybit callback runs in WS thread, schedule back to event loop
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon_threadsafe(
                        asyncio.ensure_future, self._handle_tick(ticker, message)
                    )
                except RuntimeError:
                    pass  # no running loop (shutdown)

            self._ws.ticker_stream(symbol=ticker, callback=on_tick)
            self._subscribed.add(ticker)
            logger.info("ws_subscribed", ticker=ticker)
        except Exception as e:
            logger.error("ws_subscribe_failed", ticker=ticker, error=str(e))

    async def _unsubscribe_ticker(self, ticker: str) -> None:
        """Unsubscribe from a ticker's stream."""
        try:
            self._subscribed.discard(ticker)
            await self._redis.delete(f"{REDIS_PRICE_PREFIX}:{ticker}")
            logger.info("ws_unsubscribed", ticker=ticker)
        except Exception:
            pass

    async def _handle_tick(self, ticker: str, message: dict) -> None:
        """Process a tick message and update Redis."""
        if ticker not in self._subscribed:
            return

        self._last_tick_time[ticker] = time.time()

        try:
            data = message.get("data", {})
            if not data:
                return

            # pybit ticker stream wraps data in a list
            if isinstance(data, list):
                data = data[0] if data else {}

            last_price = data.get("lastPrice") or data.get("last_price")
            if not last_price:
                return

            price_data = {
                "ticker": ticker,
                "price": float(last_price),
                "bid": float(data.get("bid1Price", 0) or data.get("bid1_price", 0) or 0),
                "ask": float(data.get("ask1Price", 0) or data.get("ask1_price", 0) or 0),
                "volume_24h": float(data.get("volume24h", 0) or data.get("turnover24h", 0) or 0),
                "ts": int(time.time() * 1000),
            }

            # Update Redis price cache (5s TTL — stale if WS disconnects)
            await self._redis.setex(
                f"{REDIS_PRICE_PREFIX}:{ticker}",
                5,
                json.dumps(price_data),
            )

            # Publish for SL engine and other subscribers
            await self._redis.publish(REDIS_TICK_CHANNEL, json.dumps(price_data))
            update_ws_health_tick()

        except Exception as e:
            logger.warning("ws_tick_handle_failed", ticker=ticker, error=str(e))
