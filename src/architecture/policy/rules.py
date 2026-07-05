"""Built-in policy rules."""
from __future__ import annotations
from .engine import Policy, PolicyResult, PolicyCategory


class TradingPolicy:
    """Trading hour and mode policies."""

    @staticmethod
    def trading_mode_check() -> Policy:
        def check(ctx):
            mode = ctx.get("trading_mode", "paper")
            if mode not in ("paper", "live"):
                return PolicyResult(False, "trading_mode", f"Invalid mode: {mode}", PolicyCategory.TRADING)
            return PolicyResult(True, "trading_mode")
        return Policy("trading_mode_check", PolicyCategory.TRADING, check, priority=10)

    @staticmethod
    def max_positions_check(max_positions: int = 5) -> Policy:
        def check(ctx):
            current = ctx.get("open_position_count", 0)
            if current >= max_positions:
                return PolicyResult(False, "max_positions",
                                  f"Max positions ({max_positions}) reached", PolicyCategory.TRADING)
            return PolicyResult(True, "max_positions")
        return Policy("max_positions_check", PolicyCategory.TRADING, check, priority=20)


class RiskPolicy:
    """Risk management policies."""

    @staticmethod
    def daily_loss_limit(limit_pct: float = 3.0) -> Policy:
        def check(ctx):
            daily_loss = ctx.get("daily_loss_pct", 0)
            if abs(daily_loss) >= limit_pct:
                return PolicyResult(False, "daily_loss_limit",
                                  f"Daily loss {daily_loss:.1f}% exceeds {limit_pct}%", PolicyCategory.RISK)
            return PolicyResult(True, "daily_loss_limit")
        return Policy("daily_loss_limit", PolicyCategory.RISK, check, priority=10)

    @staticmethod
    def max_leverage(max_lev: int = 10) -> Policy:
        def check(ctx):
            leverage = ctx.get("leverage", 1)
            if leverage > max_lev:
                return PolicyResult(False, "max_leverage",
                                  f"Leverage {leverage}x exceeds max {max_lev}x", PolicyCategory.RISK)
            return PolicyResult(True, "max_leverage")
        return Policy("max_leverage", PolicyCategory.RISK, check, priority=20)


class EmergencyPolicy:
    """Emergency policies — highest priority."""

    @staticmethod
    def kill_switch() -> Policy:
        def check(ctx):
            if ctx.get("kill_switch_active"):
                return PolicyResult(False, "kill_switch",
                                  "Kill switch active — all trading halted", PolicyCategory.EMERGENCY)
            return PolicyResult(True, "kill_switch")
        return Policy("kill_switch", PolicyCategory.EMERGENCY, check, priority=0)

    @staticmethod
    def circuit_breaker() -> Policy:
        def check(ctx):
            if ctx.get("circuit_breaker_active"):
                return PolicyResult(False, "circuit_breaker",
                                  "Circuit breaker active — trading suspended", PolicyCategory.EMERGENCY)
            return PolicyResult(True, "circuit_breaker")
        return Policy("circuit_breaker", PolicyCategory.EMERGENCY, check, priority=1)
