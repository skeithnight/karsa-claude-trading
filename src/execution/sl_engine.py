"""Karsa Trading System — WebSocket-Driven Stop-Loss Engine

Listens to Redis price tick channel from WebSocketManager.
When realtime_price <= stop_loss, fires immediate market close via SOR.
Bypasses the slow LLM/Orchestrator loop for sub-second reaction.

Flow:
  main.py starts StopLossEngine.run() as background task →
  Subscribes to karsa:events:price_tick →
  For each tick: check open positions' stop losses →
  If breached: execute market close via SOR.
"""

import asyncio
import json
import time

from src.metrics.crypto_metrics import record_sl_breach, record_sl_execution
from src.utils.logging import get_logger

logger = get_logger("sl_engine")

REDIS_TICK_CHANNEL = "karsa:events:price_tick"


class StopLossEngine:
    """Real-time stop-loss trigger driven by WebSocket price ticks."""

    def __init__(self, redis_client, bybit_client, sor=None):
        self._redis = redis_client
        self._bybit = bybit_client
        self._sor = sor
        self._running = False
        self._position_cache: dict[str, dict] = {}
        self._last_sync = 0

    async def run(self) -> None:
        """Main loop: listen for price ticks and check stop losses."""
        self._running = True
        logger.info("sl_engine_started")

        pubsub = self._redis.pubsub()
        await pubsub.subscribe(REDIS_TICK_CHANNEL)

        try:
            async for message in pubsub.listen():
                if not self._running:
                    break
                if message["type"] != "message":
                    continue
                try:
                    tick = json.loads(message["data"])
                    await self._check_tick(tick)
                except Exception as e:
                    logger.warning("sl_tick_error", error=str(e))
        except Exception as e:
            logger.error("sl_engine_error", error=str(e))
        finally:
            await pubsub.unsubscribe()
            logger.info("sl_engine_stopped")

    async def stop(self) -> None:
        self._running = False

    async def _check_tick(self, tick: dict) -> None:
        """Check if a tick breaches any position's stop loss."""
        ticker = tick.get("ticker", "")
        price = tick.get("price", 0)
        if not ticker or not price:
            return

        # Refresh position cache every 60s
        if time.time() - self._last_sync > 60:
            await self._sync_positions()

        pos = self._position_cache.get(ticker)
        if not pos:
            return

        stop_loss = pos.get("stop_loss", 0)
        side = pos.get("side", "")
        size = pos.get("size", 0)
        if not stop_loss or not size:
            return

        breached = (side == "Buy" and price <= stop_loss) or \
                   (side == "Sell" and price >= stop_loss)

        if breached:
            logger.warning("sl_breached", ticker=ticker, price=price,
                          stop_loss=stop_loss, side=side, size=size)
            record_sl_breach(ticker)
            await self._execute_close(ticker, side, size, price, stop_loss)

    async def _sync_positions(self) -> None:
        """Sync open positions and their stop losses."""
        try:
            positions = await self._bybit.get_positions()
            if positions is None:
                return

            self._position_cache.clear()
            for p in positions:
                sym = p.get("symbol", "")
                size = float(p.get("size", 0) or 0)
                if not sym or size <= 0:
                    continue

                sl_price = float(p.get("stopLoss", 0) or p.get("stop_loss", 0) or 0)
                if not sl_price:
                    sl_price = await self._fetch_stop_price(sym)

                self._position_cache[sym] = {
                    "stop_loss": sl_price,
                    "side": p.get("side", ""),
                    "size": size,
                }
            self._last_sync = time.time()
        except Exception as e:
            logger.error("sl_position_sync_failed", error=str(e))

    async def _fetch_stop_price(self, ticker: str) -> float:
        """Fetch stop-loss price from open stop orders."""
        try:
            resp = await self._bybit.get_open_orders(category="linear", symbol=ticker)
            if resp and isinstance(resp, list):
                for order in resp:
                    if order.get("stopOrderType") in ("StopLoss", "Stop"):
                        return float(order.get("triggerPrice", 0) or order.get("stopPrice", 0) or 0)
        except Exception:
            pass
        return 0.0

    async def _execute_close(self, ticker: str, side: str, size: float,
                              price: float, stop_loss: float) -> None:
        """Execute market close order."""
        close_side = "Sell" if side == "Buy" else "Buy"

        try:
            if self._sor:
                result = await self._sor.execute(
                    ticker=ticker, side=close_side,
                    quantity=size, order_type="Market",
                )
            else:
                result = await self._bybit.place_order(
                    symbol=ticker, side=close_side,
                    order_type="Market", qty=str(size),
                    reduce_only=True,
                )

            if result and result.get("orderId"):
                logger.info("sl_executed", ticker=ticker, order_id=result["orderId"])
                record_sl_execution(ticker, True)
                await self._redis.publish("karsa:events:sl_triggered", json.dumps({
                    "ticker": ticker, "trigger_price": price,
                    "stop_loss": stop_loss, "side": side,
                    "size": size, "order_id": result["orderId"],
                }))
                self._position_cache.pop(ticker, None)
            else:
                logger.error("sl_execute_failed", ticker=ticker, result=result)
                record_sl_execution(ticker, False)
        except Exception as e:
            logger.error("sl_execute_error", ticker=ticker, error=str(e))
            record_sl_execution(ticker, False)
