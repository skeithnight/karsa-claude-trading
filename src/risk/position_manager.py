"""Karsa Trading System — Position Manager

Post-entry position lifecycle management:
- Partial exits at profit targets (+1R, +2R)
- Time-based exits for stale positions (48h with <1% gain)
- SL verification & recovery (checks Bybit, recovers missing stops)

Flow:
  Scheduler calls check_partial_exits() / check_time_exits() / verify_and_recover_sl() →
  Returns action dicts → scheduler executes via SOR.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from src.advisory.crypto_technicals import calculate_atr
from src.models.database import async_session
from src.models.tables import CryptoPosition, CryptoPartialExit
from src.utils.logging import get_logger

logger = get_logger("position_manager")

# Partial exit targets (in R-multiples)
PARTIAL_EXIT_TARGETS = [
    {"r_multiple": 1.0, "exit_pct": 50, "reason": "partial_1r"},
    # Leave 50% with trailing stop
]

# Time-based exit: close positions open > 48h with < 1% gain
TIME_EXIT_MAX_HOURS = 48
TIME_EXIT_MIN_GAIN_PCT = Decimal("1.0")


class PositionManager:
    """Post-entry position lifecycle management."""

    def __init__(self, bybit, redis_client):
        self.bybit = bybit
        self._redis = redis_client

    async def check_partial_exits(self, positions: list[CryptoPosition]) -> list[dict]:
        """Check all open positions for partial exit opportunities.

        Returns list of exit actions to execute.
        """
        actions = []

        for pos in positions:
            if pos.status != "OPEN":
                continue
            if pos.partial_exits_taken >= len(PARTIAL_EXIT_TARGETS):
                continue  # all partials taken

            try:
                # Calculate current R-multiple
                entry_price = Decimal(str(pos.entry_price))
                current_price = Decimal(str(pos.current_price or 0))
                stop_loss = Decimal(str(pos.stop_loss or 0))

                # Fallback: use trailing stop if primary SL is missing
                if stop_loss == 0:
                    stop_loss = Decimal(str(pos.trailing_stop_price or 0))

                if stop_loss == 0 or entry_price == 0:
                    continue

                # Risk = distance from entry to stop
                if pos.side == "Buy":
                    risk = entry_price - stop_loss
                    reward = current_price - entry_price
                else:
                    risk = stop_loss - entry_price
                    reward = entry_price - current_price

                if risk <= 0:
                    continue

                r_multiple = reward / risk

                # Check next target
                next_target = PARTIAL_EXIT_TARGETS[pos.partial_exits_taken]
                if r_multiple >= Decimal(str(next_target["r_multiple"])):
                    actions.append({
                        "position_id": pos.id,
                        "ticker": pos.ticker,
                        "action": "partial_exit",
                        "exit_pct": next_target["exit_pct"],
                        "reason": next_target["reason"],
                        "r_multiple": float(r_multiple),
                        "entry_price": str(entry_price),
                        "current_price": str(current_price),
                        "stop_loss": str(stop_loss),
                    })

                    logger.info("partial_exit_triggered",
                                ticker=pos.ticker,
                                r_multiple=float(r_multiple),
                                exit_pct=next_target["exit_pct"],
                                reason=next_target["reason"])

            except Exception as e:
                logger.error("partial_exit_check_failed", ticker=pos.ticker, error=str(e))

        return actions

    async def execute_partial_exit(self, position_id: int, exit_pct: int, reason: str) -> dict:
        """Execute a partial exit on a position.

        Returns execution result dict.
        """
        try:
            async with async_session() as session:
                pos = await session.get(CryptoPosition, position_id)
                if not pos or pos.status != "OPEN":
                    return {"success": False, "error": "Position not found or not open"}

                # Calculate exit quantity
                exit_qty = Decimal(str(pos.size)) * Decimal(exit_pct) / Decimal(100)
                if exit_qty <= 0:
                    return {"success": False, "error": "Zero exit quantity"}

                # Execute via SOR with reduce_only
                from src.risk.sor import SmartOrderRouter
                sor = SmartOrderRouter(self.bybit)

                # Build a reduce-only signal
                exit_side = "Sell" if pos.side == "Buy" else "Buy"
                result = await sor.execute_order(
                    signal={
                        "ticker": pos.ticker,
                        "direction": "CLOSE",
                        "confidence": 100,
                    },
                    risk_params={
                        "qty": float(exit_qty),
                        "leverage": pos.leverage,
                        "reduce_only": True,
                    },
                )

                if result.get("success"):
                    # Update position
                    exit_price = Decimal(str(result.get("fill_price", pos.current_price)))
                    pnl_per_unit = (exit_price - Decimal(str(pos.entry_price))) * (1 if pos.side == "Buy" else -1)
                    pnl_usdt = pnl_per_unit * exit_qty

                    # Position Manager promotion (Phase 3 — single writer)
                    from src.architecture.feature_flags import flags
                    if flags.is_enabled("position_manager_enabled"):
                        from src.architecture.position import PositionManager, PartialExit as PartialExitCmd
                        from src.architecture.events import event_bus
                        arch_pm = PositionManager(event_bus=event_bus)
                        cmd = PartialExitCmd(
                            position_id=f"db:{position_id}",
                            exit_quantity=float(exit_qty),
                            exit_price=float(exit_price),
                            reason=reason,
                        )
                        await arch_pm.partial_exit(cmd)
                        logger.info("position_manager_write", ticker=pos.ticker, exit_pct=exit_pct)
                    else:
                        # Legacy direct DB write
                        pos.size = Decimal(str(pos.size)) - exit_qty
                        pos.partial_exits_taken = pos.partial_exits_taken + 1
                        pos.last_management_check = datetime.now(timezone.utc)

                        session.add(CryptoPartialExit(
                            position_id=position_id,
                            exit_pct=exit_pct,
                            exit_price=exit_price,
                            exit_qty=exit_qty,
                            pnl_usdt=pnl_usdt,
                            reason=reason,
                        ))

                        try:
                            from src.models.tables import ClosedPaperTrade
                            pnl_pct = float((exit_price - pos.entry_price) / pos.entry_price * 100) if pos.entry_price else 0
                            if pos.side == "Sell":
                                pnl_pct = -pnl_pct

                            session.add(ClosedPaperTrade(
                                ticker=pos.ticker,
                                market="CRYPTO",
                                side=pos.side,
                                quantity=exit_qty,
                                entry_price=pos.entry_price,
                                exit_price=exit_price,
                                realized_pnl=pnl_usdt,
                                realized_pnl_pct=pnl_pct,
                                entry_date=pos.opened_at,
                                exit_date=datetime.now(timezone.utc),
                                exit_reason=reason
                            ))
                            from src.metrics.crypto_metrics import record_trade_close
                            record_trade_close(float(pnl_usdt), "win" if pnl_usdt > 0 else "loss")
                        except Exception as e:
                            logger.error("closed_paper_trade_insert_failed", error=str(e))

                        if pos.size <= Decimal("0.00000001"):
                            pos.status = "CLOSED"

                    await session.commit()

                    logger.info("partial_exit_executed",
                                ticker=pos.ticker,
                                exit_pct=exit_pct,
                                exit_qty=str(exit_qty),
                                exit_price=str(exit_price),
                                pnl=str(pnl_usdt))

                    # Publish business event (shadow mode)
                    from src.architecture.events import publish_event
                    await publish_event(
                        "PositionReduced" if pos.status == "OPEN" else "PositionClosed",
                        aggregate_id=pos.ticker,
                        aggregate_type="Position",
                        payload={
                            "ticker": pos.ticker,
                            "side": pos.side,
                            "exit_pct": exit_pct,
                            "exit_qty": str(exit_qty),
                            "exit_price": str(exit_price),
                            "pnl_usdt": str(pnl_usdt),
                            "reason": reason,
                            "status": pos.status,
                        },
                        publisher="PositionManager",
                    )

                    return {
                        "success": True,
                        "ticker": pos.ticker,
                        "exit_pct": exit_pct,
                        "exit_qty": str(exit_qty),
                        "exit_price": str(exit_price),
                        "pnl_usdt": str(pnl_usdt),
                        "remaining_size": str(pos.size),
                        "reason": reason,
                    }
                else:
                    return {"success": False, "error": result.get("error", "SOR failed")}

        except Exception as e:
            logger.error("partial_exit_execute_failed", position_id=position_id, error=str(e))
            return {"success": False, "error": str(e)}

    async def check_time_exits(self, positions: list[CryptoPosition]) -> list[dict]:
        """Check for stale positions that should be closed.

        Closes positions open > 72h with < 1% gain (capital efficiency).
        Returns list of close actions.
        """
        actions = []
        now = datetime.now(timezone.utc)

        for pos in positions:
            if pos.status != "OPEN":
                continue

            try:
                # Check age
                opened_at = pos.opened_at
                if opened_at.tzinfo is None:
                    from datetime import timezone as tz
                    opened_at = opened_at.replace(tzinfo=tz.utc)

                hours_open = (now - opened_at).total_seconds() / 3600
                if hours_open < TIME_EXIT_MAX_HOURS:
                    continue

                # Check gain
                entry_price = Decimal(str(pos.entry_price))
                current_price = Decimal(str(pos.current_price or 0))
                if entry_price == 0:
                    continue

                if pos.side == "Buy":
                    gain_pct = ((current_price - entry_price) / entry_price) * 100
                else:
                    gain_pct = ((entry_price - current_price) / entry_price) * 100

                if gain_pct >= TIME_EXIT_MIN_GAIN_PCT:
                    continue  # profitable enough, let it run

                actions.append({
                    "position_id": pos.id,
                    "ticker": pos.ticker,
                    "action": "time_exit",
                    "hours_open": round(hours_open, 1),
                    "gain_pct": float(gain_pct),
                    "reason": f"stale_{int(hours_open)}h_{float(gain_pct):.1f}pct",
                })

                logger.info("time_exit_triggered",
                            ticker=pos.ticker,
                            hours_open=round(hours_open, 1),
                            gain_pct=float(gain_pct))

            except Exception as e:
                logger.error("time_exit_check_failed", ticker=pos.ticker, error=str(e))

        return actions

    # --- SL Verification & Recovery (Phase 1) ---

    async def verify_and_recover_sl(self, positions: list[CryptoPosition]) -> list[dict]:
        """Verify all open positions have active SL orders on Bybit.

        If SL is missing (rejected, timeout, etc.): recalculate from ATR, place via Bybit, update DB.
        Returns list of recovery actions taken.
        """
        recoveries = []
        for pos in positions:
            if pos.status != "OPEN":
                continue
            try:
                has_sl = await self._check_bybit_sl(pos.ticker)
                if has_sl:
                    continue

                # SL missing — recover from ATR
                atr = await self._get_current_atr(pos.ticker)
                if not atr:
                    logger.warning("sl_recovery_no_atr", ticker=pos.ticker)
                    continue

                sl_price = self._calculate_sl_from_atr(pos, atr)
                result = await self.bybit.set_stop_loss(pos.ticker, float(sl_price), pos.side)
                if result.get("error"):
                    logger.error("sl_recovery_failed", ticker=pos.ticker, error=result["error"])
                    continue

                # Update DB
                async with async_session() as session:
                    db_pos = await session.get(CryptoPosition, pos.id)
                    if db_pos:
                        db_pos.trailing_stop_price = float(sl_price)
                        db_pos.last_management_check = datetime.utcnow()
                        await session.commit()

                recoveries.append({
                    "ticker": pos.ticker,
                    "action": "sl_recovered",
                    "sl_price": float(sl_price),
                    "atr": atr,
                })
                logger.warning("sl_recovered", ticker=pos.ticker, sl=float(sl_price), atr=atr)

            except Exception as e:
                logger.error("sl_recovery_check_failed", ticker=pos.ticker, error=str(e))

        return recoveries

    async def _check_bybit_sl(self, ticker: str) -> bool:
        """Check if Bybit has an active StopLoss order for this ticker."""
        try:
            orders = await self.bybit.get_open_orders(ticker)
            if not orders:
                return False
            for o in orders:
                if o.get("stopOrderType") == "StopLoss":
                    return True
            return False
        except Exception as e:
            logger.warning("bybit_sl_check_failed", ticker=ticker, error=str(e))
            return True  # fail-open: assume SL exists if we can't check

    async def _get_current_atr(self, ticker: str) -> float | None:
        """Fetch current ATR from 4h OHLCV (14 periods)."""
        try:
            ohlcv = await self.bybit.get_ohlcv(ticker, interval="4h", limit=15)
            if not ohlcv or len(ohlcv) < 15:
                return None
            atr_data = calculate_atr(ohlcv)
            return float(atr_data["atr"])
        except Exception as e:
            logger.warning("atr_fetch_failed", ticker=ticker, error=str(e))
            return None

    def _calculate_sl_from_atr(self, pos: CryptoPosition, atr: float) -> Decimal:
        """Calculate SL price: entry ± 1.5x ATR (conservative default)."""
        entry = Decimal(str(pos.entry_price))
        distance = Decimal(str(atr)) * Decimal("1.5")
        if pos.side == "Buy":
            return entry - distance
        return entry + distance
