"""Karsa Trading System — Position Manager

Post-entry position lifecycle management:
- Partial exits at profit targets (+1R, +2R)
- Time-based exits for stale positions (72h with <1% gain)

Flow:
  Scheduler calls check_partial_exits() / check_time_exits() →
  Returns action dicts → scheduler executes via SOR.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

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

                    pos.size = Decimal(str(pos.size)) - exit_qty
                    pos.partial_exits_taken = pos.partial_exits_taken + 1
                    pos.last_management_check = datetime.now(timezone.utc)

                    # Log partial exit
                    session.add(CryptoPartialExit(
                        position_id=position_id,
                        exit_pct=exit_pct,
                        exit_price=exit_price,
                        exit_qty=exit_qty,
                        pnl_usdt=pnl_usdt,
                        reason=reason,
                    ))

                    # If fully exited, close position
                    if pos.size <= Decimal("0.00000001"):
                        pos.status = "CLOSED"

                    await session.commit()

                    logger.info("partial_exit_executed",
                                ticker=pos.ticker,
                                exit_pct=exit_pct,
                                exit_qty=str(exit_qty),
                                exit_price=str(exit_price),
                                pnl=str(pnl_usdt))

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
