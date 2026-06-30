"""IDX market compliance rules — all order validation goes through here."""

from datetime import date, timedelta

# IDX tick price tiers (Fraksi Harga Saham)
_TIERS: list[tuple[float, int]] = [
    (200, 1),
    (500, 2),
    (2_000, 5),
    (5_000, 10),
    (float("inf"), 25),
]


def tick_size(price: float) -> int:
    """Return the tick size for a given price level."""
    for ceiling, tick in _TIERS:
        if price < ceiling:
            return tick
    return 25


def round_to_tick(price: float) -> int:
    """Round price to nearest valid IDX limit order price."""
    t = tick_size(price)
    return int(round(price / t) * t)


def ara_ceiling(prev_close: float) -> float:
    """Auto Rejection Above — 25% above previous close."""
    return prev_close * 1.25


def arb_floor(prev_close: float) -> float:
    """Auto Rejection Below — 25% below previous close."""
    return prev_close * 0.75


def max_lots_by_adv(adv_20d, price, max_adv_pct=0.10):
    """Calculate max lots based on 20-day Average Daily Volume.

    Ensures position does not exceed max_adv_pct (default 10%) of ADV.
    If exit door is smaller than position, do not enter.

    Args:
        adv_20d: 20-day average daily volume in shares
        price: Entry price per share
        max_adv_pct: Max position as fraction of ADV (default 0.10 = 10%)

    Returns:
        Maximum lots (1 lot = 100 shares)
    """
    if adv_20d <= 0 or price <= 0:
        return 0
    max_shares = adv_20d * max_adv_pct
    max_lots = int(max_shares // 100)
    return max(0, max_lots)


def validate_order(ticker, price, prev_close, lots, adv_20d=None, max_adv_pct=0.10):
    """Raise ValueError if order violates IDX market rules.

    Args:
        ticker: Stock ticker
        price: Order price
        prev_close: Previous closing price
        lots: Order size in lots (1 lot = 100 shares)
        adv_20d: 20-day average daily volume in shares (optional, enables ADV gate)
        max_adv_pct: Max position as fraction of ADV (default 10%)
    """
    if lots < 1:
        raise ValueError(f"{ticker}: minimum 1 lot (100 shares), got {lots}")
    rounded = round_to_tick(price)
    if abs(price - rounded) > 0.001:
        raise ValueError(f"{ticker}: {price} is not a valid tick price — use {rounded}")
    if price > ara_ceiling(prev_close):
        raise ValueError(f"{ticker}: {price:,} exceeds ARA ceiling {ara_ceiling(prev_close):,.0f}")
    if price < arb_floor(prev_close):
        raise ValueError(f"{ticker}: {price:,} below ARB floor {arb_floor(prev_close):,.0f}")
    if adv_20d is not None and adv_20d > 0:
        max_allowed = max_lots_by_adv(adv_20d, price, max_adv_pct)
        if max_allowed < 1:
            raise ValueError(f"{ticker}: ADV too low ({adv_20d:,.0f} shares) — no valid position size at 10% ADV")
        if lots > max_allowed:
            raise ValueError(
                f"{ticker}: {lots} lots exceeds 10% ADV limit ({max_allowed} lots). "
                f"ADV={adv_20d:,.0f} shares, max position={max_allowed * 100:,.0f} shares"
            )


def settlement_date(trade_date: date) -> date:
    """IDX T+2: next 2 trading days (weekdays only)."""
    d, count = trade_date, 0
    while count < 2:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d


def is_settled(trade_date: date) -> bool:
    """Check if a trade has settled (T+2)."""
    return date.today() >= settlement_date(trade_date)


# --- Dynamic ARA/ARB ---

# Per-ticker ARA/ARB overrides (extendable via DB or config).
# Default is 25% per IDX rules. Some tickers (e.g. newly listed, restructuring)
# may have different limits set by IDX.
_TICKER_ARA_ARB: dict[str, float] = {
    "default": 0.25,
    # Example overrides (populate from IDX announcements):
    # "GOTO": 0.35,
}


def ara_ceiling_dynamic(prev_close: float, ticker: str | None = None) -> float:
    """Dynamic ARA ceiling — per-ticker when available, else 25% default."""
    pct = _TICKER_ARA_ARB.get(ticker.upper(), _TICKER_ARA_ARB["default"]) if ticker else _TICKER_ARA_ARB["default"]
    return prev_close * (1 + pct)


def arb_floor_dynamic(prev_close: float, ticker: str | None = None) -> float:
    """Dynamic ARB floor — per-ticker when available, else 25% default."""
    pct = _TICKER_ARA_ARB.get(ticker.upper(), _TICKER_ARA_ARB["default"]) if ticker else _TICKER_ARA_ARB["default"]
    return prev_close * (1 - pct)


# --- IHSG Circuit Breaker ---

IDX_CIRCUIT_BREAKERS: list[tuple[float, int | None]] = [
    (0.05, 30),    # ±5% → 30 min trading halt
    (0.10, None),  # ±10% → trading halted for the day
]


def ihsg_circuit_breaker_level(ihsg_pct_change: float) -> dict:
    """Check if IHSG movement triggers a circuit breaker.

    Args:
        ihsg_pct_change: IHSG change as decimal (0.05 = +5%, -0.07 = -7%)

    Returns:
        {triggered: bool, halt_minutes: int|None, level: int, description: str}
    """
    abs_change = abs(ihsg_pct_change)
    for i, (threshold, halt_minutes) in enumerate(IDX_CIRCUIT_BREAKERS):
        if abs_change >= threshold:
            desc = f"{'Halted' if halt_minutes is None else f'{halt_minutes}-min halt'}"
            return {
                "triggered": True,
                "halt_minutes": halt_minutes,
                "level": i + 1,
                "threshold_pct": threshold * 100,
                "description": f"IHSG {ihsg_pct_change:+.2%} — Level {i+1} circuit breaker: {desc}",
            }
    return {"triggered": False, "halt_minutes": 0, "level": 0, "description": "No circuit breaker triggered"}


# --- Forced Sell Triggers ---

FORCED_SELL_RULES = [
    {
        "id": "3x_lower_limit",
        "trigger": "3x_lower_limit_hit",
        "description": "Stock hits lower auto-rejection limit 3 consecutive days",
        "action": "forced_sell",
        "universe": "full",
    },
    {
        "id": "unusual_volume_10x",
        "trigger": "unusual_volume_10x",
        "description": "Volume exceeds 10x 20-day ADV (potential insider/dump)",
        "action": "forced_sell",
        "universe": "full",
    },
    {
        "id": "t2_settlement_failure",
        "trigger": "t2_settlement_failure",
        "description": "T+2 settlement not met (funds not available)",
        "action": "forced_sell",
        "universe": "full",
    },
    {
        "id": "idx_audit_suspend",
        "trigger": "idx_audit_suspend",
        "description": "IDX or OJK suspends trading for audit/investigation",
        "action": "forced_sell",
        "universe": "full",
    },
]


def check_forced_sell_triggers(
    ticker: str,
    position: dict | None = None,
    market_data: dict | None = None,
) -> dict:
    """Check if a position should be force-liquidated.

    Args:
        ticker: Stock ticker
        position: {entry_price, quantity, consecutive_lower_limits}
        market_data: {current_volume, adv_20d, is_suspended, settlement_ok}

    Returns:
        {triggered: bool, rule_id: str|None, action: str|None, description: str}
    """
    pos = position or {}
    mkt = market_data or {}

    # 3x lower limit
    consec_lower = pos.get("consecutive_lower_limits", 0)
    if consec_lower >= 3:
        return {
            "triggered": True,
            "rule_id": "3x_lower_limit",
            "action": "forced_sell",
            "description": f"{ticker}: hit lower ARB limit {consec_lower} consecutive days — forced liquidation",
        }

    # Unusual volume 10x ADV
    current_vol = mkt.get("current_volume", 0)
    adv_20d = mkt.get("adv_20d", 0)
    if adv_20d > 0 and current_vol > adv_20d * 10:
        return {
            "triggered": True,
            "rule_id": "unusual_volume_10x",
            "action": "forced_sell",
            "description": f"{ticker}: volume {current_vol:,} is {current_vol / adv_20d:.1f}x ADV ({adv_20d:,}) — forced liquidation",
        }

    # T+2 settlement failure
    if mkt.get("settlement_ok") is False:
        return {
            "triggered": True,
            "rule_id": "t2_settlement_failure",
            "action": "forced_sell",
            "description": f"{ticker}: T+2 settlement failure — forced liquidation",
        }

    # IDX audit suspension
    if mkt.get("is_suspended"):
        return {
            "triggered": True,
            "rule_id": "idx_audit_suspend",
            "action": "forced_sell",
            "description": f"{ticker}: trading suspended by IDX/OJK — forced liquidation pending resume",
        }

    return {"triggered": False, "rule_id": None, "action": None, "description": "No forced sell triggers"}
