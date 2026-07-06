"""Position commands — mutations go through command pattern."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OpenPosition:
    symbol: str
    side: str  # LONG | SHORT
    entry_price: float
    quantity: float
    leverage: int = 1
    stop_loss: Optional[float] = None
    market: str = "CRYPTO"


@dataclass
class UpdateStopLoss:
    position_id: str
    new_stop_loss: float
    reason: str = ""


@dataclass
class ClosePosition:
    position_id: str
    exit_price: float
    reason: str = ""


@dataclass
class PartialExit:
    position_id: str
    exit_quantity: float
    exit_price: float
    reason: str = ""


@dataclass
class UpdateTrailingStop:
    """Trailing stop tightened by trailing_stop or profit_lock."""
    position_id: str
    new_trail_stop: float
    highest_price: Optional[float] = None
    regime: str = ""
    reason: str = ""


@dataclass
class UpdateCurrentPrice:
    """Mark-to-market price update."""
    position_id: str
    current_price: float


@dataclass
class RecoverStopLoss:
    """SL recovered after missing on exchange."""
    position_id: str
    sl_price: float
    atr: float = 0.0


@dataclass
class SyncFromExchange:
    """Reconciliation: size/status synced from exchange."""
    position_id: str
    exchange_size: float
    exchange_status: str = ""
