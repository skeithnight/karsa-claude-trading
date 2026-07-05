"""Position aggregate — single writer principle.

State machine: CREATED→OPENING→OPEN→BREAK_EVEN→PARTIAL_EXIT→TRAILING→EXITING→CLOSED
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from ..common.base import AggregateRoot


class PositionState(str, Enum):
    CREATED = "CREATED"
    OPENING = "OPENING"
    OPEN = "OPEN"
    BREAK_EVEN = "BREAK_EVEN"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    TRAILING = "TRAILING"
    EXITING = "EXITING"
    CLOSED = "CLOSED"


# Valid state transitions
_TRANSITIONS = {
    PositionState.CREATED: {PositionState.OPENING},
    PositionState.OPENING: {PositionState.OPEN, PositionState.CLOSED},
    PositionState.OPEN: {PositionState.BREAK_EVEN, PositionState.PARTIAL_EXIT, PositionState.TRAILING, PositionState.EXITING, PositionState.CLOSED},
    PositionState.BREAK_EVEN: {PositionState.TRAILING, PositionState.PARTIAL_EXIT, PositionState.EXITING, PositionState.CLOSED},
    PositionState.PARTIAL_EXIT: {PositionState.TRAILING, PositionState.EXITING, PositionState.CLOSED},
    PositionState.TRAILING: {PositionState.EXITING, PositionState.CLOSED},
    PositionState.EXITING: {PositionState.CLOSED},
    PositionState.CLOSED: set(),
}


@dataclass
class Position(AggregateRoot):
    symbol: str = ""
    side: str = "LONG"  # LONG | SHORT
    entry_price: float = 0.0
    quantity: float = 0.0
    leverage: int = 1
    stop_loss: Optional[float] = None
    trailing_stop: Optional[float] = None
    state: PositionState = PositionState.CREATED
    market: str = "CRYPTO"
    pnl_realized: float = 0.0
    pnl_unrealized: float = 0.0
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    def transition(self, target: PositionState):
        allowed = _TRANSITIONS.get(self.state, set())
        if target not in allowed:
            raise ValueError(f"Invalid transition: {self.state} -> {target}")
        self.state = target
        self.bump_version()

    def update_unrealized_pnl(self, mark_price: float):
        if self.side == "LONG":
            self.pnl_unrealized = (mark_price - self.entry_price) * self.quantity
        else:
            self.pnl_unrealized = (self.entry_price - mark_price) * self.quantity
