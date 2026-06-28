"""Karsa Trading System - Volatility Targeted Position Sizing"""

from decimal import Decimal

def calculate_position_size(equity: float, risk_per_trade_pct: float, entry_price: float, atr: float, stop_multiplier: float = 2.0) -> Decimal:
    """Calculate position size based on ATR volatility targeting.

    Args:
        equity: Total portfolio equity
        risk_per_trade_pct: Percentage of equity to risk per trade (e.g., 0.01 for 1%)
        entry_price: Planned entry price
        atr: Current Average True Range (usually 14-day)
        stop_multiplier: Multiplier for ATR to set stop distance

    Returns:
        Decimal representing quantity of shares/contracts to buy
    """
    if atr <= 0 or entry_price <= 0 or equity <= 0:
        return Decimal(0)

    risk_amount = equity * risk_per_trade_pct
    stop_distance = atr * stop_multiplier

    if stop_distance == 0:
        return Decimal(0)

    position_size = risk_amount / stop_distance
    return Decimal(str(position_size))

def calculate_stop_loss(entry_price: float, atr: float, side: str = "LONG", stop_multiplier: float = 2.0) -> float:
    """Calculate stop loss price based on entry and ATR.

    Args:
        entry_price: Planned entry price
        atr: Current Average True Range
        side: "LONG" or "SHORT"
        stop_multiplier: Multiplier for ATR

    Returns:
        Stop loss price
    """
    stop_distance = atr * stop_multiplier

    if side == "LONG":
        return entry_price - stop_distance
    else:
        return entry_price + stop_distance
