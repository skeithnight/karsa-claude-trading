"""Karsa Trading System — Trailing Stop Manager

Adjusts stop-loss orders upward for winning positions.
Uses ATR-based trailing with regime-aware multipliers.

Flow:
  Scheduler calls update_trailing_stops() every 5 min →
  For each open position: fetch price, update highest_price, recalculate stop →
  If stop changed: amend order on Bybit, log to crypto_trailing_stops.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from src.advisory.crypto_technicals import calculate_atr
from src.advisory.crypto_universe import PAIR_CONFIG
from src.config import settings
from src.models.database import async_session
from src.models.tables import CryptoPosition, CryptoTrailingStop
from src.utils.logging import get_logger
from sqlalchemy import select

logger = get_logger("trailing_stop")

# Regime → ATR multiplier for trailing distance
REGIME_TRAIL_MULTIPLIER = {
    "TREND_BULL": 2.0,
    "TREND_BEAR": 2.0,
    "MEAN_REVERSION": 1.5,
    "CHOP": 0,  # disabled in chop
}

# Redis cooldown key prefix — prevents rapid stop amendments
COOLDOWN_KEY_PREFIX = "karsa:trailing_stop_cooldown"
COOLDOWN_SEC = 300  # 5 min between amendments per ticker


class TrailingStopManager:
    """Adjusts trailing stop-losses for open crypto positions."""

    def __init__(self, bybit, redis_client):
        self.bybit = bybit
        self._redis = redis_client

    async def update_trailing_stops(self, positions: list[CryptoPosition]) -> list[dict]:
        """Update trailing stops for all open positions.

        Returns list of actions taken for logging/alerting.
        """
        actions = []

        for pos in positions:
            if pos.status != "OPEN":
                continue

            # Orphan detection: if DB has no trailing stop, sync from Bybit
            if not pos.trailing_stop_price:
                synced = await self._sync_orphan_sl(pos)
                if synced:
                    actions.append(synced)
                    continue  # will trail on next cycle once DB is populated
                # Neither DB nor Bybit has SL — create one from ATR (recovery)
                recovered = await self._create_initial_stop(pos)
                if recovered:
                    actions.append(recovered)
                continue

            regime = pos.regime_at_entry or "TREND_BULL"
            multiplier = REGIME_TRAIL_MULTIPLIER.get(regime, 2.0)
            if multiplier == 0:
                logger.debug("trailing_disabled_chop", ticker=pos.ticker)
                continue

            try:
                # Fetch current price from Bybit
                current_price = await self._get_current_price(pos.ticker)
                if current_price is None:
                    continue

                # Update highest price
                entry_price = Decimal(str(pos.entry_price))
                current = Decimal(str(current_price))
                old_highest = pos.highest_price or entry_price
                new_highest = max(old_highest, current)
                max_sl_pct = Decimal(str(settings.CRYPTO_MAX_SL_PCT)) / 100
                max_distance = current * max_sl_pct

                # Calculate trailing distance
                if settings.CRYPTO_SL_MODE == "fixed":
                    trail_distance = min(Decimal(str(settings.CRYPTO_FIXED_SL_DISTANCE)), max_distance)
                    atr = Decimal("1.0")  # placeholder for breakeven floor calc
                else:
                    ohlcv = await self._get_ohlcv(pos.ticker)
                    if not ohlcv or len(ohlcv) < 15:
                        continue
                    atr_data = calculate_atr(ohlcv)
                    atr = Decimal(str(atr_data["atr"]))
                    trail_distance = atr * Decimal(str(multiplier))
                    trail_distance = min(trail_distance, max_distance)
                if pos.side == "Buy":
                    new_trail_stop = new_highest - trail_distance
                else:
                    new_trail_stop = new_highest + trail_distance

                # Ensure trailing stop doesn't go below entry (breakeven floor)
                if pos.side == "Buy":
                    new_trail_stop = max(new_trail_stop, entry_price + atr * Decimal("0.1"))
                else:
                    new_trail_stop = min(new_trail_stop, entry_price - atr * Decimal("0.1"))

                # Guard: SL must be on correct side of current price
                if pos.side == "Buy" and new_trail_stop >= current:
                    continue  # price dropped below breakeven floor, keep initial SL
                if pos.side == "Sell" and new_trail_stop <= current:
                    continue

                # Check if stop actually changed
                old_stop = pos.trailing_stop_price
                if old_stop and abs(new_trail_stop - Decimal(str(old_stop))) < atr * Decimal("0.05"):
                    # Stop change is < 5% of ATR — noise, skip
                    continue

                # Check Redis cooldown
                if not await self._check_cooldown(pos.ticker):
                    logger.debug("trailing_cooldown", ticker=pos.ticker)
                    continue

                # Amend stop on Bybit
                success = await self._amend_stop_on_exchange(pos, new_trail_stop)
                if not success:
                    continue

                # Position Manager promotion (Phase 3 — single writer)
                from src.architecture.feature_flags import flags
                if flags.is_enabled("position_manager_enabled"):
                    from src.architecture.position import PositionManager, UpdateTrailingStop
                    from src.architecture.events import event_bus
                    arch_pm = PositionManager(event_bus=event_bus)
                    cmd = UpdateTrailingStop(
                        position_id=f"db:{pos.id}",
                        new_trail_stop=new_trail_stop,
                        highest_price=new_highest,
                        regime=regime,
                    )
                    await arch_pm.update_trailing_stop(cmd)
                    logger.info("position_manager_write", ticker=pos.ticker, trail=new_trail_stop)
                else:
                    # Legacy direct DB write
                    async with async_session() as session:
                        db_pos = await session.get(CryptoPosition, pos.id)
                        if db_pos:
                            db_pos.trailing_stop_price = new_trail_stop
                            db_pos.highest_price = new_highest
                            db_pos.last_management_check = datetime.utcnow()

                            # Log adjustment
                            session.add(CryptoTrailingStop(
                                position_id=pos.id,
                                old_price=old_stop,
                                new_price=new_trail_stop,
                                trigger_price=current,
                                reason=f"trail_{regime.lower()}",
                            ))
                            await session.commit()

                # Set cooldown
                await self._set_cooldown(pos.ticker)

                actions.append({
                    "ticker": pos.ticker,
                    "action": "trailing_stop_updated",
                    "old_stop": str(old_stop) if old_stop else None,
                    "new_stop": str(new_trail_stop),
                    "highest_price": str(new_highest),
                    "current_price": str(current),
                    "regime": regime,
                })

                logger.info("trailing_stop_updated",
                            ticker=pos.ticker,
                            old=str(old_stop),
                            new=str(new_trail_stop),
                            highest=str(new_highest))

                # Publish business event (shadow mode)
                from src.architecture.events import publish_event
                await publish_event(
                    "TrailingActivated",
                    aggregate_id=pos.ticker,
                    aggregate_type="Position",
                    payload={
                        "ticker": pos.ticker,
                        "old_stop": str(old_stop) if old_stop else None,
                        "new_stop": str(new_trail_stop),
                        "highest_price": str(new_highest),
                        "regime": regime,
                    },
                    publisher="TrailingStopManager",
                )

            except Exception as e:
                logger.error("trailing_stop_failed", ticker=pos.ticker, error=str(e))

        return actions

    async def _sync_orphan_sl(self, pos: CryptoPosition) -> dict | None:
        """Sync a Bybit SL order to DB when trailing_stop_price is None.

        Returns action dict if synced, None if no orphan found.
        """
        try:
            orders = await self.bybit.get_open_orders(pos.ticker)
            sl_order = None
            for o in orders:
                if o.get("stopOrderType") == "StopLoss":
                    sl_order = o
                    break
            if not sl_order:
                return None

            sl_price = float(sl_order.get("triggerPrice", 0))
            if sl_price <= 0:
                return None

            # Sync to DB
            async with async_session() as session:
                db_pos = await session.get(CryptoPosition, pos.id)
                if db_pos:
                    db_pos.trailing_stop_price = sl_price
                    db_pos.last_management_check = datetime.utcnow()
                    await session.commit()

            logger.info("orphan_sl_synced", ticker=pos.ticker, sl=sl_price)
            return {
                "ticker": pos.ticker,
                "action": "orphan_sl_synced",
                "sl_price": sl_price,
            }
        except Exception as e:
            logger.warning("orphan_sl_sync_failed", ticker=pos.ticker, error=str(e))
            return None

    async def _create_initial_stop(self, pos: CryptoPosition) -> dict | None:
        """Create trailing stop when no SL exists (recovery path)."""
        try:
            entry = Decimal(str(pos.entry_price))
            max_sl_pct = Decimal(str(settings.CRYPTO_MAX_SL_PCT)) / 100
            max_distance = entry * max_sl_pct

            if settings.CRYPTO_SL_MODE == "fixed":
                distance = min(Decimal(str(settings.CRYPTO_FIXED_SL_DISTANCE)), max_distance)
            else:
                ohlcv = await self._get_ohlcv(pos.ticker)
                if not ohlcv or len(ohlcv) < 15:
                    return None
                atr_data = calculate_atr(ohlcv)
                atr = Decimal(str(atr_data["atr"]))
                distance = atr * Decimal("1.5")
                distance = min(distance, max_distance)

            if pos.side == "Buy":
                sl_price = entry - distance
            else:
                sl_price = entry + distance

            result = await self.bybit.set_stop_loss(pos.ticker, float(sl_price), pos.side)
            if result.get("error"):
                logger.warning("create_initial_stop_failed", ticker=pos.ticker, error=result["error"])
                return None

            async with async_session() as session:
                db_pos = await session.get(CryptoPosition, pos.id)
                if db_pos:
                    db_pos.trailing_stop_price = float(sl_price)
                    db_pos.last_management_check = datetime.utcnow()
                    await session.commit()

            logger.warning("initial_stop_created", ticker=pos.ticker, sl=float(sl_price), atr=float(atr))
            return {
                "ticker": pos.ticker,
                "action": "initial_stop_created",
                "sl_price": float(sl_price),
                "atr": float(atr),
            }
        except Exception as e:
            logger.error("create_initial_stop_error", ticker=pos.ticker, error=str(e))
            return None

    async def _get_current_price(self, ticker: str) -> float | None:
        """Get current mark price from Bybit."""
        try:
            resp = await asyncio.to_thread(
                self.bybit._http_client.get_tickers,
                category="linear",
                symbol=ticker,
            )
            if resp.get("retCode") == 0:
                items = resp.get("result", {}).get("list", [])
                if items:
                    return float(items[0].get("lastPrice", 0))
        except Exception as e:
            logger.warning("price_fetch_failed", ticker=ticker, error=str(e))
        return None

    async def _get_ohlcv(self, ticker: str) -> list[dict] | None:
        """Fetch recent 1h OHLCV for ATR calculation."""
        try:
            resp = await asyncio.to_thread(
                self.bybit._http_client.get_kline,
                category="linear",
                symbol=ticker,
                interval="60",
                limit=20,
            )
            if resp.get("retCode") == 0:
                items = resp.get("result", {}).get("list", [])
                return [
                    {"open": float(k[1]), "high": float(k[2]),
                     "low": float(k[3]), "close": float(k[4]),
                     "volume": float(k[5])}
                    for k in reversed(items)
                ]
        except Exception as e:
            logger.warning("ohlcv_fetch_failed", ticker=ticker, error=str(e))
        return None

    async def _amend_stop_on_exchange(self, pos: CryptoPosition, new_stop: Decimal) -> bool:
        """Amend the stop-loss on Bybit via set_trading_stop (correct API for SL)."""
        try:
            tick = PAIR_CONFIG.get(pos.ticker, {}).get("tick_size", 0.001)
            rounded_stop = float(new_stop)
            rounded_stop = round(rounded_stop, len(str(tick).rstrip('0').split('.')[-1]))

            resp = await asyncio.to_thread(
                self.bybit._http_client.set_trading_stop,
                category="linear",
                symbol=pos.ticker,
                stopLoss=str(rounded_stop),
            )
            if resp.get("retCode") != 0:
                logger.warning("amend_stop_failed", ticker=pos.ticker, ret=resp.get("retMsg"))
                return False
            return True

        except Exception as e:
            logger.error("amend_stop_failed", ticker=pos.ticker, error=str(e))
            return False

    async def _check_cooldown(self, ticker: str) -> bool:
        """Check if ticker is in cooldown period."""
        if not self._redis:
            return True
        try:
            key = f"{COOLDOWN_KEY_PREFIX}:{ticker}"
            val = await self._redis.get(key)
            return val is None
        except Exception:
            return True  # fail-open on Redis errors

    async def _set_cooldown(self, ticker: str) -> None:
        """Set cooldown for ticker."""
        if not self._redis:
            return
        try:
            key = f"{COOLDOWN_KEY_PREFIX}:{ticker}"
            await self._redis.setex(key, COOLDOWN_SEC, "1")
        except Exception:
            pass
