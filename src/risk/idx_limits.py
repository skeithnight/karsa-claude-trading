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


def validate_order(ticker: str, price: float, prev_close: float, lots: int) -> None:
    """Raise ValueError if order violates IDX market rules."""
    if lots < 1:
        raise ValueError(f"{ticker}: minimum 1 lot (100 shares), got {lots}")
    rounded = round_to_tick(price)
    if price != rounded:
        raise ValueError(f"{ticker}: {price} is not a valid tick price — use {rounded}")
    if price > ara_ceiling(prev_close):
        raise ValueError(f"{ticker}: {price:,} exceeds ARA ceiling {ara_ceiling(prev_close):,.0f}")
    if price < arb_floor(prev_close):
        raise ValueError(f"{ticker}: {price:,} below ARB floor {arb_floor(prev_close):,.0f}")


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
