"""Exit strategy interface and decision types."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ExitDecision(str, Enum):
    CONTINUE = "CONTINUE"          # No exit needed
    UPDATE_SL = "UPDATE_SL"        # Move stop loss
    PARTIAL_EXIT = "PARTIAL_EXIT"  # Reduce position
    FULL_EXIT = "FULL_EXIT"        # Close entire position
    EMERGENCY_EXIT = "EMERGENCY_EXIT"  # Immediate close


@dataclass
class ExitSignal:
    decision: ExitDecision
    strategy_name: str
    reason: str
    exit_price: Optional[float] = None
    exit_quantity_pct: float = 1.0  # 1.0 = full exit
    new_stop_loss: Optional[float] = None
    priority: int = 0  # lower = higher priority


class ExitStrategy(ABC):
    """Strategy interface — each exit rule implements this."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def priority(self) -> int: ...

    @abstractmethod
    def evaluate(self, position, market_data: dict) -> Optional[ExitSignal]:
        """Evaluate if this strategy triggers an exit.

        Args:
            position: Position aggregate
            market_data: {mark_price, atr, regime, funding_rate, ...}

        Returns:
            ExitSignal if action needed, None to continue.
        """
        ...
