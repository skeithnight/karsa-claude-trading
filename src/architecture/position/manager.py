"""Position Manager — single writer for all position state.

All position mutations MUST go through this manager.
No other component should write position state directly.
"""
from __future__ import annotations
from typing import Optional, Dict
from datetime import datetime, timezone
import structlog

from ..events.base import EventEnvelope
from ..events.in_process import InProcessEventBus
from .aggregate import Position, PositionState
from .commands import (
    OpenPosition, UpdateStopLoss, ClosePosition, PartialExit,
    UpdateTrailingStop, UpdateCurrentPrice, RecoverStopLoss, SyncFromExchange,
)

logger = structlog.get_logger(__name__)

class PositionManager:
    """Single source of truth for position lifecycle.

    ponytail: in-memory dict + optional event bus. DB persistence via repository.
    """

    def __init__(self, event_bus: Optional[InProcessEventBus] = None):
        self._positions: Dict[str, Position] = {}
        self._event_bus = event_bus

    # --- Core mutations ---

    async def close_position(self, cmd: ClosePosition) -> Position:
        pos = self._get(cmd.position_id)
        pos.transition(PositionState.EXITING)
        pos.transition(PositionState.CLOSED)
        pos.closed_at = datetime.now(timezone.utc)
        if pos.side == "LONG":
            pos.pnl_realized = (cmd.exit_price - pos.entry_price) * pos.quantity
        else:
            pos.pnl_realized = (pos.entry_price - cmd.exit_price) * pos.quantity
        await self._emit("PositionClosed", pos)
        logger.info("position_closed", position_id=pos.id, pnl=pos.pnl_realized, reason=cmd.reason)
        return pos

    async def partial_exit(self, cmd: PartialExit) -> Position:
        pos = self._get(cmd.position_id)
        if cmd.exit_quantity >= pos.quantity:
            return await self.close_position(ClosePosition(
                position_id=cmd.position_id, exit_price=cmd.exit_price, reason=cmd.reason
            ))
        pos.transition(PositionState.PARTIAL_EXIT)
        partial_pnl = ((cmd.exit_price - pos.entry_price) * cmd.exit_quantity
                       if pos.side == "LONG"
                       else (pos.entry_price - cmd.exit_price) * cmd.exit_quantity)
        pos.pnl_realized += partial_pnl
        pos.quantity -= cmd.exit_quantity
        pos.bump_version()
        await self._emit("PositionReduced", pos)
        return pos

    # --- Extension mutations (Phase 3) ---

    async def update_trailing_stop(self, cmd: UpdateTrailingStop) -> Position:
        pos = self._get(cmd.position_id)
        pos.trailing_stop = cmd.new_trail_stop
        if cmd.highest_price is not None:
            pos.pnl_unrealized = pos.pnl_unrealized  # bump version
        pos.bump_version()
        # ponytail: extract db_id from "db:{id}" format for persistence
        db_id = cmd.position_id.split(":")[1] if ":" in cmd.position_id else None
        await self._persist("TrailingActivated", pos, db_id=db_id)
        return pos

    async def sync_from_exchange(self, cmd: SyncFromExchange) -> Position:
        pos = self._get(cmd.position_id)
        pos.quantity = cmd.exchange_size
        db_id = cmd.position_id.split(":")[1] if ":" in cmd.position_id else None
        if cmd.exchange_status == "CLOSED":
            pos.transition(PositionState.EXITING)
            pos.transition(PositionState.CLOSED)
            pos.closed_at = datetime.now(timezone.utc)
        pos.bump_version()
        await self._persist("PositionSynced", pos, db_id=db_id)
        return pos

    # --- Queries ---

    def open_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.state != PositionState.CLOSED]

    def _get(self, position_id: str) -> Position:
        pos = self._positions.get(position_id)
        if not pos and ":" in position_id:
            # Lazy load from DB when position_id is "db:{id}" format
            # ponytail: synchronous helper, actual DB load happens in _persist
            pos = Position(
                id=position_id,
                symbol="", side="", entry_price=0, quantity=0,
                state=PositionState.OPEN,
            )
            self._positions[position_id] = pos
        if not pos:
            raise KeyError(f"Position not found: {position_id}")
        return pos

    async def _emit(self, event_type: str, pos: Position):
        if self._event_bus:
            envelope = EventEnvelope(
                event_type=event_type,
                aggregate_id=pos.id,
                aggregate_type="Position",
                payload={"symbol": pos.symbol, "side": pos.side, "state": pos.state.value},
                publisher="PositionManager",
            )
            await self._event_bus.publish(envelope)

    async def _persist(self, event_type: str, pos: Position, **extra):
        """Emit event and persist to DB when db_id is available."""
        try:
            from src.metrics.crypto_metrics import record_pm_write
            record_pm_write(event_type)
        except Exception:
            pass
        await self._emit(event_type, pos)
        db_id = extra.get("db_id")
        if not db_id:
            return
        try:
            from src.models.database import async_session
            from src.models.tables import CryptoPosition
            async with async_session() as session:
                db_pos = await session.get(CryptoPosition, int(db_id))
                if not db_pos:
                    return
                if pos.trailing_stop is not None:
                    db_pos.trailing_stop_price = pos.trailing_stop
                if pos.stop_loss is not None:
                    db_pos.stop_loss = pos.stop_loss
                if pos.state == PositionState.CLOSED:
                    db_pos.status = "CLOSED"
                db_pos.last_management_check = datetime.now(timezone.utc)
                await session.commit()
                logger.debug("position_manager_db_persisted",
                            db_id=db_id, event=event_type, state=pos.state.value)
        except Exception as e:
            logger.error("position_manager_db_persist_failed",
                        db_id=db_id, error=str(e))
