"""Karsa Trading System - Market Hours & Holiday Management"""

from datetime import datetime, time, date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.tables import MarketHoliday
from src.utils.logging import get_logger

logger = get_logger("market_hours")

IDX_TZ = ZoneInfo("Asia/Jakarta")
US_TZ = ZoneInfo("America/New_York")

IDX_OPEN = time(9, 0)
IDX_CLOSE = time(15, 30)
IDX_LUNCH_START = time(12, 0)
IDX_LUNCH_END = time(13, 30)

US_OPEN = time(9, 30)
US_CLOSE = time(16, 0)


def is_idx_open(now: datetime | None = None) -> bool:
    """Check if IDX market is currently open."""
    if now is None:
        now = datetime.now(IDX_TZ)
    else:
        now = now.astimezone(IDX_TZ)

    if now.weekday() >= 5:
        return False

    current_time = now.time()
    if not (IDX_OPEN <= current_time <= IDX_CLOSE):
        return False
    if IDX_LUNCH_START <= current_time <= IDX_LUNCH_END:
        return False

    return True


def is_us_open(now: datetime | None = None) -> bool:
    """Check if US market is currently open."""
    if now is None:
        now = datetime.now(US_TZ)
    else:
        now = now.astimezone(US_TZ)

    if now.weekday() >= 5:
        return False

    current_time = now.time()
    if not (US_OPEN <= current_time <= US_CLOSE):
        return False

    return True


def get_market_tz(market: str) -> ZoneInfo:
    """Get timezone for a market."""
    if market == "IDX":
        return IDX_TZ
    elif market in ("US", "ETF"):
        return US_TZ
    raise ValueError(f"Unknown market: {market}")


def get_next_market_open(market: str) -> datetime:
    """Get the next market open time."""
    tz = get_market_tz(market)
    now = datetime.now(tz)

    if market == "IDX":
        open_time = IDX_OPEN
    else:
        open_time = US_OPEN

    if (market == "IDX" and is_idx_open(now)) or (market in ("US", "ETF") and is_us_open(now)):
        next_day = now.date() + timedelta(days=1)
    else:
        next_day = now.date()

    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)

    return datetime.combine(next_day, open_time, tzinfo=tz)


async def is_holiday(market: str, session: AsyncSession, check_date: date | None = None) -> bool:
    """Check if a given date is a market holiday."""
    if check_date is None:
        tz = get_market_tz(market)
        check_date = datetime.now(tz).date()

    # Cast the DateTime column to Date for comparison
    from sqlalchemy import cast, Date
    result = await session.execute(
        select(MarketHoliday).where(
            MarketHoliday.market == market,
            cast(MarketHoliday.holiday_date, Date) == check_date,
        )
    )
    return result.scalar_one_or_none() is not None


async def should_scan_market(market: str, session: AsyncSession) -> bool:
    """Check if we should scan a market (open and not holiday)."""
    if market == "IDX":
        if not is_idx_open():
            return False
    elif market in ("US", "ETF"):
        if not is_us_open():
            return False

    if await is_holiday(market, session):
        logger.info("market_holiday", market=market)
        return False

    return True
