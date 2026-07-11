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
WATCHDOG_INTERVAL_SEC = 3
WS_DEAD_THRESHOLD_SEC = 10


class StopLossEngine:
    """Real-time stop-loss trigger driven by WebSocket price ticks."""

    def __init__(self, redis_client, bybit_client, sor=None):
        self._redis = redis_client
        self._bybit = bybit_client
        self._sor = sor
        self._running = False
        self._position_cache: dict[str, dict] = {}
        self._last_sync = 0
        self._last_tick_time: dict[str, float] = {}
        self._rest_fallback_active: set[str] = set()

    async def run(self) -> None:
        """Main loop: listen for price ticks with watchdog REST fallback."""
        self._running = True
        logger.info("sl_engine_started")

        # Start watchdog alongside pubsub listener
        watchdog_task = asyncio.create_task(self._watchdog())

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
            watchdog_task.cancel()
            await pubsub.unsubscribe()
            await pubsub.close()  # Finding 3: release the dedicated Redis connection
            logger.info("sl_engine_stopped")

    async def stop(self) -> None:
        self._running = False

    async def _watchdog(self) -> None:
        """Detect dead WS feeds and poll REST as fallback for SL checks."""
        while self._running:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL_SEC)
                if not self._position_cache:
                    continue

                now = time.time()
                for ticker in list(self._position_cache):
                    last = self._last_tick_time.get(ticker, 0)
                    silence = now - last if last else float("inf")

                    if silence > WS_DEAD_THRESHOLD_SEC:
                        if ticker not in self._rest_fallback_active:
                            self._rest_fallback_active.add(ticker)
                            logger.warning("sl_ws_dead_rest_fallback", ticker=ticker,
                                           silence=f"{silence:.0f}s")
                        # ponytail: REST poll — bybit_client.get_ticker already exists
                        ticker_data = await self._bybit.get_ticker(ticker)
                        if ticker_data and ticker_data.get("price"):
                            # Direct SL check — don't go through _check_tick
                            # which would update _last_tick_time and confuse
                            # the watchdog into thinking WS recovered
                            await self._evaluate_sl(ticker, ticker_data["price"])
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("sl_watchdog_error", error=str(e))

    async def _check_tick(self, tick: dict) -> None:
        """Check if a tick breaches any position's stop loss."""
        ticker = tick.get("ticker", "")
        price = tick.get("price", 0)
        if not ticker or not price:
            return

        # Track last tick time for watchdog; mark recovery from fallback
        self._last_tick_time[ticker] = time.time()
        if ticker in self._rest_fallback_active:
            self._rest_fallback_active.discard(ticker)
            logger.info("ws_feed_recovered", ticker=ticker)

        await self._evaluate_sl(ticker, price)

    async def _evaluate_sl(self, ticker: str, price: float) -> None:
        """Evaluate stop-loss for a ticker at given price."""
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
                    "entry_price": float(p.get("entry_price", 0) or p.get("avgPrice", 0) or 0),
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

    async def _record_sl_pnl(self, ticker: str, side: str, size: float,
                              trigger_price: float, order_id: str) -> None:
        """Record PnL for a stop-loss close in ClosedPaperTrade."""
        try:
            from datetime import datetime, timezone
            from decimal import Decimal
            from src.models.database import async_session
            from src.models.tables import ClosedPaperTrade, CryptoPosition
            from sqlalchemy import select

            # Get actual fill price from Bybit
            fill_price = trigger_price
            try:
                status = await self._bybit.get_order_status(ticker, order_id)
                if status and status.get("avg_price"):
                    fill_price = float(status["avg_price"])
            except Exception:
                pass  # use trigger_price as fallback

            # Get entry price from cache or DB
            cached = self._position_cache.get(ticker, {})
            entry_price = cached.get("entry_price", 0)

            if not entry_price:
                # Fallback: fetch from DB
                try:
                    async with async_session() as session:
                        result = await session.execute(
                            select(CryptoPosition).where(
                                CryptoPosition.ticker == ticker,
                                CryptoPosition.status == "OPEN",
                            ).order_by(CryptoPosition.id.desc()).limit(1)
                        )
                        db_pos = result.scalar_one_or_none()
                        if db_pos:
                            entry_price = float(db_pos.entry_price)
                            # Update DB position status
                            db_pos.status = "CLOSED"
                            db_pos.last_synced_at = datetime.now(timezone.utc)
                            await session.commit()
                except Exception:
                    pass

            if not entry_price:
                logger.warning("sl_pnl_no_entry_price", ticker=ticker)
                return

            # Calculate PnL
            from src.models.database import async_session as _session
            async with _session() as session:
                # Update position status if still open
                result = await session.execute(
                    select(CryptoPosition).where(
                        CryptoPosition.ticker == ticker,
                        CryptoPosition.status == "OPEN",
                    )
                )
                db_pos = result.scalar_one_or_none()
                if db_pos:
                    db_pos.status = "CLOSED"
                    db_pos.last_synced_at = datetime.now(timezone.utc)

                pnl_per_unit = (fill_price - entry_price) if side == "Buy" else (entry_price - fill_price)
                pnl_usdt = pnl_per_unit * size
                pnl_pct = (pnl_per_unit / entry_price * 100) if entry_price else 0

                session.add(ClosedPaperTrade(
                    ticker=ticker,
                    market="CRYPTO",
                    side="LONG" if side == "Buy" else "SHORT",
                    quantity=Decimal(str(size)),
                    entry_price=Decimal(str(entry_price)),
                    exit_price=Decimal(str(fill_price)),
                    realized_pnl=Decimal(str(pnl_usdt)),
                    realized_pnl_pct=Decimal(str(round(pnl_pct, 4))),
                    exit_reason=f"stop_loss:{order_id}",
                ))
                await session.commit()

                from src.metrics.crypto_metrics import record_trade_close
                record_trade_close(
                    pnl_usdt,
                    "win" if pnl_usdt > 0 else "loss",
                    ticker=ticker,
                    exit_price=fill_price,
                    closed_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                )

                logger.info("sl_pnl_recorded",
                            ticker=ticker,
                            entry_price=entry_price,
                            exit_price=fill_price,
                            pnl=round(pnl_usdt, 4),
                            pnl_pct=round(pnl_pct, 2))

        except Exception as e:
            logger.error("sl_pnl_record_failed", ticker=ticker, error=str(e))

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

            order_id = result.get("orderId") or result.get("order_id")
            if result and order_id:
                logger.info("sl_executed", ticker=ticker, order_id=order_id)
                record_sl_execution(ticker, True)
                await self._redis.publish("karsa:events:sl_triggered", json.dumps({
                    "ticker": ticker, "trigger_price": price,
                    "stop_loss": stop_loss, "side": side,
                    "size": size, "order_id": order_id,
                }))

                # Record PnL in DB
                await self._record_sl_pnl(ticker, side, size, price, order_id)

                self._position_cache.pop(ticker, None)

                # Publish business event (shadow mode)
                from src.architecture.events import publish_event
                await publish_event(
                    "StopLossTriggered",
                    aggregate_id=ticker,
                    aggregate_type="Position",
                    payload={
                        "ticker": ticker,
                        "trigger_price": price,
                        "stop_loss": stop_loss,
                        "side": side,
                        "size": size,
                        "order_id": order_id,
                    },
                    publisher="StopLossEngine",
                )
            else:
                logger.error("sl_execute_failed", ticker=ticker, result=result)
                record_sl_execution(ticker, False)
        except Exception as e:
            logger.error("sl_execute_error", ticker=ticker, error=str(e))
            record_sl_execution(ticker, False)
