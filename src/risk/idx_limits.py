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
