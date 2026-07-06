"""Concrete exit strategies."""
from __future__ import annotations
from typing import Optional
from datetime import datetime, timezone, timedelta
from .base import ExitStrategy, ExitSignal, ExitDecision


class EmergencyExitStrategy(ExitStrategy):
    """Priority 0 — kill switch, circuit breaker."""
    name = "emergency"
    priority = 0

    def evaluate(self, position, market_data: dict) -> Optional[ExitSignal]:
        if market_data.get("kill_switch_active"):
            return ExitSignal(ExitDecision.EMERGENCY_EXIT, self.name, "Kill switch active", priority=self.priority)
        return None


class StopLossStrategy(ExitStrategy):
    """Priority 10 — fixed stop loss hit."""
    name = "stop_loss"
    priority = 10

    def evaluate(self, position, market_data: dict) -> Optional[ExitSignal]:
        mark_price = market_data.get("mark_price", 0)
        if not position.stop_loss or not mark_price:
            return None
        if position.side == "LONG" and mark_price <= position.stop_loss:
            return ExitSignal(ExitDecision.FULL_EXIT, self.name,
                            f"SL hit: {mark_price} <= {position.stop_loss}", priority=self.priority)
        if position.side == "SHORT" and mark_price >= position.stop_loss:
            return ExitSignal(ExitDecision.FULL_EXIT, self.name,
                            f"SL hit: {mark_price} >= {position.stop_loss}", priority=self.priority)
        return None


class TimeExitStrategy(ExitStrategy):
    """Priority 20 — stale position exit (48h, <1% gain)."""
    name = "time_exit"
    priority = 20

    def __init__(self, max_hours: float = 48.0, min_gain_pct: float = 1.0):
        self.max_hours = max_hours
        self.min_gain_pct = min_gain_pct

    def evaluate(self, position, market_data: dict) -> Optional[ExitSignal]:
        if not position.opened_at:
            return None
        age = datetime.now(timezone.utc) - position.opened_at
        if age < timedelta(hours=self.max_hours):
            return None
        mark_price = market_data.get("mark_price", position.entry_price)
        if position.side == "LONG":
            gain_pct = ((mark_price - position.entry_price) / position.entry_price) * 100
        else:
            gain_pct = ((position.entry_price - mark_price) / position.entry_price) * 100
        if gain_pct < self.min_gain_pct:
            return ExitSignal(ExitDecision.FULL_EXIT, self.name,
                            f"Stale {age.total_seconds()/3600:.0f}h, gain {gain_pct:.1f}% < {self.min_gain_pct}%",
                            priority=self.priority)
        return None


class TrailingStopStrategy(ExitStrategy):
    """Priority 30 — ATR-based trailing stop."""
    name = "trailing_stop"
    priority = 30

    def __init__(self, atr_multiplier: float = 2.0):
        self.atr_multiplier = atr_multiplier

    def evaluate(self, position, market_data: dict) -> Optional[ExitSignal]:
        if not position.trailing_stop:
            return None
        mark_price = market_data.get("mark_price", 0)
        if not mark_price:
            return None
        if position.side == "LONG" and mark_price <= position.trailing_stop:
            return ExitSignal(ExitDecision.FULL_EXIT, self.name,
                            f"Trailing hit: {mark_price} <= {position.trailing_stop}",
                            priority=self.priority)
        if position.side == "SHORT" and mark_price >= position.trailing_stop:
            return ExitSignal(ExitDecision.FULL_EXIT, self.name,
                            f"Trailing hit: {mark_price} >= {position.trailing_stop}",
                            priority=self.priority)
        return None


class BreakEvenStrategy(ExitStrategy):
    """Priority 40 — move SL to break-even after +1R."""
    name = "break_even"
    priority = 40

    def __init__(self, trigger_r: float = 1.0):
        self.trigger_r = trigger_r

    def evaluate(self, position, market_data: dict) -> Optional[ExitSignal]:
        atr = market_data.get("atr", 0)
        mark_price = market_data.get("mark_price", 0)
        if not atr or not mark_price or not position.stop_loss:
            return None
        if position.side == "LONG":
            gain_r = (mark_price - position.entry_price) / atr if atr else 0
            if gain_r >= self.trigger_r and position.stop_loss < position.entry_price:
                return ExitSignal(ExitDecision.UPDATE_SL, self.name,
                                f"+{gain_r:.1f}R reached, move SL to BE",
                                new_stop_loss=position.entry_price, priority=self.priority)
        else:
            gain_r = (position.entry_price - mark_price) / atr if atr else 0
            if gain_r >= self.trigger_r and position.stop_loss > position.entry_price:
                return ExitSignal(ExitDecision.UPDATE_SL, self.name,
                                f"+{gain_r:.1f}R reached, move SL to BE",
                                new_stop_loss=position.entry_price, priority=self.priority)
        return None


class PartialExitStrategy(ExitStrategy):
    """Priority 50 — partial exit at +1R target (50%)."""
    name = "partial_exit"
    priority = 50

    def __init__(self, target_r: float = 1.0, exit_pct: float = 0.5):
        self.target_r = target_r
        self.exit_pct = exit_pct

    def evaluate(self, position, market_data: dict) -> Optional[ExitSignal]:
        atr = market_data.get("atr", 0)
        mark_price = market_data.get("mark_price", 0)
        if not atr or not mark_price:
            return None
        if position.side == "LONG":
            gain_r = (mark_price - position.entry_price) / atr if atr else 0
        else:
            gain_r = (position.entry_price - mark_price) / atr if atr else 0
        if gain_r >= self.target_r:
            return ExitSignal(ExitDecision.PARTIAL_EXIT, self.name,
                            f"+{gain_r:.1f}R reached, partial exit {self.exit_pct*100:.0f}%",
                            exit_quantity_pct=self.exit_pct, priority=self.priority)
        return None
