"""Exit Engine — centralized exit decision making."""
from .base import ExitStrategy, ExitDecision, ExitSignal
from .engine import ExitEngine
from .strategies import StopLossStrategy, BreakEvenStrategy, TrailingStopStrategy, TimeExitStrategy, PartialExitStrategy, EmergencyExitStrategy

__all__ = ["ExitStrategy", "ExitDecision", "ExitSignal", "ExitEngine",
           "StopLossStrategy", "BreakEvenStrategy", "TrailingStopStrategy",
           "TimeExitStrategy", "PartialExitStrategy", "EmergencyExitStrategy"]
