"""Exit Engine — orchestrates all exit strategies by priority.

Priority order: Emergency → Liquidation → StopLoss → TimeExit → Trailing → PartialExit → BreakEven
"""
from __future__ import annotations
from typing import List, Optional
import structlog

from .base import ExitStrategy, ExitDecision, ExitSignal

logger = structlog.get_logger(__name__)


class ExitEngine:
    """Central exit decision maker. All exit logic runs through here."""

    def __init__(self):
        self._strategies: List[ExitStrategy] = []

    def register(self, strategy: ExitStrategy):
        self._strategies.append(strategy)
        self._strategies.sort(key=lambda s: s.priority)

    def evaluate(self, position, market_data: dict) -> ExitSignal:
        """Run all strategies in priority order, return first non-CONTINUE."""
        for strategy in self._strategies:
            signal = strategy.evaluate(position, market_data)
            if signal and signal.decision != ExitDecision.CONTINUE:
                logger.info("exit_triggered",
                           strategy=strategy.name,
                           decision=signal.decision.value,
                           reason=signal.reason)
                return signal
        return ExitSignal(
            decision=ExitDecision.CONTINUE,
            strategy_name="none",
            reason="No exit condition met",
        )
