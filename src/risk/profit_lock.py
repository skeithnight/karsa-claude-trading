"""Karsa Trading System — Profit Lock Engine

Tiered profit protection that tightens stops as unrealized gain increases.
Prevents giving back profits on winning positions.

Tiers:
  +0.5R → move SL to breakeven (entry price)
  +1.0R → tight trail: current price - 1.0x ATR
  +2.0R → medium trail: current price - 0.75x ATR
  +3.0R → tight trail: current price - 0.5x ATR

Flow:
  Called from _job_update_trailing_stops after normal trailing →
  For each position: calculate R-multiple → determine tier →
  If tier suggests tighter stop than current: amend on Bybit, update DB.
"""

from decimal import Decimal
from src.advisory.crypto_technicals import calculate_atr
from src.models.database import async_session
from src.models.tables import CryptoPosition, CryptoTrailingStop
from src.utils.logging import get_logger
from datetime import datetime

logger = get_logger("profit_lock")

# Profit lock tiers: min_r → stop formula
PROFIT_TIERS = [
    {"min_r": 0.5, "atr_mult": None, "desc": "breakeven"},
    {"min_r": 1.0, "atr_mult": Decimal("1.0"), "desc": "tight_trail_1.0"},
    {"min_r": 2.0, "atr_mult": Decimal("0.75"), "desc": "medium_trail_0.75"},
    {"min_r": 3.0, "atr_mult": Decimal("0.5"), "desc": "tight_trail_0.5"},
]


class ProfitLockManager:
    """Tiered profit protection — tightens stops as gain increases."""

    def __init__(self, bybit, redis_client):
        self.bybit = bybit
        self._redis = redis_client

    async def check_profit_locks(self, positions: list[CryptoPosition]) -> list[dict]:
        """Check all positions for profit lock triggers.

        Returns list of actions taken.
        """
        actions = []
        for pos in positions:
            if pos.status != "OPEN":
                continue
            if not pos.trailing_stop_price:
                continue  # no baseline to lock from
            if pos.partial_exits_taken > 0:
                continue  # already partially exited, let trailing handle

            try:
                action = await self._evaluate_position(pos)
                if action:
                    actions.append(action)
            except Exception as e:
                logger.error("profit_lock_error", ticker=pos.ticker, error=str(e))

        return actions

    async def _evaluate_position(self, pos: CryptoPosition) -> dict | None:
        """Evaluate one position for profit lock."""
        entry = Decimal(str(pos.entry_price))
        current = Decimal(str(pos.current_price or 0))
        # Use original stop_loss for R-multiple (not trailing_stop which may already be in profit)
        stop_loss = Decimal(str(pos.stop_loss or 0))
        if stop_loss == 0:
            stop_loss = Decimal(str(pos.trailing_stop_price or 0))

        if entry == 0 or current == 0:
            return None

        # Calculate R-multiple (reward / risk)
        if pos.side == "Buy":
            risk = entry - stop_loss
            reward = current - entry
        else:
            risk = stop_loss - entry
            reward = entry - current

        if risk <= 0:
            return None

        r_multiple = reward / risk

        # Find applicable tier
        tier = self._get_tier(r_multiple)
        if not tier:
            return None  # below +0.5R, no lock needed

        # Calculate proposed stop
        atr = await self._get_atr(pos.ticker)
        if not atr:
            return None

        proposed_stop = self._calculate_stop(pos, tier, atr, entry, current)

        # Only update if proposed stop is tighter than CURRENT trailing stop
        current_trail = Decimal(str(pos.trailing_stop_price or 0))
        if current_trail > 0:
            if pos.side == "Buy":
                if proposed_stop <= current_trail:
                    return None  # trailing stop already tighter
            else:
                if proposed_stop >= current_trail:
                    return None

        # Amend on Bybit
        success = await self._amend_stop(pos.ticker, proposed_stop, pos.side)
        if not success:
            return None

        # Position Manager promotion (Phase 3 — single writer)
        from src.architecture.feature_flags import flags
        if flags.is_enabled("position_manager_enabled"):
            from src.architecture.position import PositionManager, UpdateTrailingStop
            from src.architecture.events import event_bus
            arch_pm = PositionManager(event_bus=event_bus)
            cmd = UpdateTrailingStop(
                position_id=f"db:{pos.id}",
                new_trail_stop=float(proposed_stop),
                regime=tier["desc"],
            )
            await arch_pm.update_trailing_stop(cmd)
            logger.info("position_manager_write", ticker=pos.ticker, trail=float(proposed_stop))
        else:
            # Legacy direct DB write
            async with async_session() as session:
                db_pos = await session.get(CryptoPosition, pos.id)
                if db_pos:
                    old_stop = db_pos.trailing_stop_price
                    db_pos.trailing_stop_price = float(proposed_stop)
                    db_pos.last_management_check = datetime.utcnow()
                    session.add(CryptoTrailingStop(
                        position_id=pos.id,
                        old_price=old_stop,
                        new_price=float(proposed_stop),
                        trigger_price=float(current),
                        reason=f"profit_lock_{tier['desc']}",
                    ))
                    await session.commit()

        logger.info("profit_lock_activated",
                     ticker=pos.ticker,
                     r_multiple=float(r_multiple),
                     tier=tier["desc"],
                     old_stop=str(stop_loss),
                     new_stop=str(proposed_stop))

        # Publish business event (shadow mode)
        from src.architecture.events import publish_event
        await publish_event(
            "BreakEvenActivated",
            aggregate_id=pos.ticker,
            aggregate_type="Position",
            payload={
                "ticker": pos.ticker,
                "tier": tier["desc"],
                "r_multiple": float(r_multiple),
                "old_stop": str(stop_loss),
                "new_stop": str(proposed_stop),
            },
            publisher="ProfitLockManager",
        )

        return {
            "ticker": pos.ticker,
            "action": "profit_lock",
            "tier": tier["desc"],
            "r_multiple": float(r_multiple),
            "old_stop": str(stop_loss),
            "new_stop": str(proposed_stop),
        }

    def _get_tier(self, r_multiple: Decimal) -> dict | None:
        """Find the highest applicable tier for this R-multiple."""
        result = None
        for tier in PROFIT_TIERS:
            if r_multiple >= Decimal(str(tier["min_r"])):
                result = tier
        return result

    def _calculate_stop(self, pos, tier, atr, entry, current) -> Decimal:
        """Calculate stop price based on tier."""
        atr_dist = Decimal(str(atr))

        if tier["atr_mult"] is None:
            # Breakeven tier — stop at entry
            return entry

        trail_dist = atr_dist * tier["atr_mult"]

        if pos.side == "Buy":
            return current - trail_dist
        else:
            return current + trail_dist

    async def _get_atr(self, ticker: str) -> float | None:
        """Fetch current ATR from 1h OHLCV."""
        try:
            ohlcv = await self.bybit.get_ohlcv(ticker, interval="1h", limit=20)
            if not ohlcv or len(ohlcv) < 15:
                return None
            atr_data = calculate_atr(ohlcv)
            return float(atr_data["atr"])
        except Exception as e:
            logger.warning("profit_lock_atr_failed", ticker=ticker, error=str(e))
            return None

    async def _amend_stop(self, ticker: str, new_stop: Decimal, side: str) -> bool:
        """Amend stop loss on Bybit via set_stop_loss (replaces existing SL)."""
        try:
            result = await self.bybit.set_stop_loss(ticker, float(new_stop), side)
            if result.get("error"):
                logger.warning("profit_lock_amend_failed", ticker=ticker, error=result["error"])
                return False
            return True
        except Exception as e:
            logger.warning("profit_lock_amend_failed", ticker=ticker, error=str(e))
            return False
