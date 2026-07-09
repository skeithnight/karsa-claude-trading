"""Karsa Trading System — Position Snapshot Utility

Shared dataclass for creating immutable position snapshots from database ORM objects.
Used by both main.py and main_crypto.py to avoid code duplication.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class PositionSnapshot:
    """Immutable snapshot of a CryptoPosition for use after DB session closes."""

    id: int
    ticker: str
    side: str
    status: str
    size: Decimal
    entry_price: Decimal
    current_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    trailing_stop_price: Optional[Decimal] = None
    highest_price: Optional[Decimal] = None
    leverage: Optional[int] = None
    regime_at_entry: Optional[str] = None
    signal_source: Optional[str] = None
    opened_at: Optional[datetime] = None
    partial_exits_taken: int = 0


def snapshot_from_db(db_pos) -> PositionSnapshot:
    """Create a PositionSnapshot from a CryptoPosition ORM object.

    Extracts all needed columns inside the session to avoid lazy-load
    or event-loop-mismatch errors after session closes.
    """
    return PositionSnapshot(
        id=db_pos.id,
        ticker=db_pos.ticker,
        side=db_pos.side,
        status=db_pos.status,
        size=db_pos.size,
        entry_price=db_pos.entry_price,
        current_price=db_pos.current_price,
        stop_loss=db_pos.stop_loss,
        trailing_stop_price=db_pos.trailing_stop_price,
        highest_price=db_pos.highest_price,
        leverage=db_pos.leverage,
        regime_at_entry=db_pos.regime_at_entry,
        signal_source=db_pos.signal_source,
        opened_at=db_pos.opened_at,
        partial_exits_taken=db_pos.partial_exits_taken,
    )
