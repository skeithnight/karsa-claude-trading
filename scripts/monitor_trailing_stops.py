"""Monitor trailing stops from open crypto positions.

Usage:
    python -m scripts.monitor_trailing_stops

Shows:
    - All open positions with trailing stop status
    - Current price vs entry vs trailing stop
    - Distance to trailing stop (in % and absolute)
    - Dynamic stop percentage (if set)
    - Recent trailing stop adjustments
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from src.models.database import async_session
from src.models.tables import CryptoPosition, CryptoTrailingStop
from sqlalchemy import select, desc


async def get_open_positions():
    """Fetch all open positions with trailing stop info."""
    async with async_session() as session:
        result = await session.execute(
            select(CryptoPosition)
            .where(CryptoPosition.status == "OPEN")
            .order_by(desc(CryptoPosition.opened_at))
        )
        return result.scalars().all()


async def get_recent_trailing_adjustments(position_id: int, limit: int = 5):
    """Fetch recent trailing stop adjustments for a position."""
    async with async_session() as session:
        result = await session.execute(
            select(CryptoTrailingStop)
            .where(CryptoTrailingStop.position_id == position_id)
            .order_by(desc(CryptoTrailingStop.created_at))
            .limit(limit)
        )
        return result.scalars().all()


def calculate_distance(entry: Decimal, current: Decimal, trailing_stop: Decimal, side: str) -> dict:
    """Calculate distance metrics."""
    if entry == 0 or current == 0:
        return {"pct": 0, "abs": 0, "to_trailing_pct": 0, "to_trailing_abs": 0}

    # Unrealized P&L %
    if side == "Buy":
        pnl_pct = float((current - entry) / entry * 100)
    else:
        pnl_pct = float((entry - current) / entry * 100)

    # Distance to trailing stop
    if trailing_stop:
        if side == "Buy":
            to_stop_abs = float(current - trailing_stop)
            to_stop_pct = to_stop_abs / float(current) * 100
        else:
            to_stop_abs = float(trailing_stop - current)
            to_stop_pct = to_stop_abs / float(current) * 100
    else:
        to_stop_abs = 0
        to_stop_pct = 0

    return {
        "pnl_pct": pnl_pct,
        "pnl_abs": float(current - entry) if side == "Buy" else float(entry - current),
        "to_trailing_pct": to_stop_pct,
        "to_trailing_abs": to_stop_abs,
    }


async def print_position_status(pos):
    """Print detailed status for one position."""
    # Calculate hours held
    now = datetime.now(timezone.utc)
    opened = pos.opened_at
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    hours_held = (now - opened).total_seconds() / 3600

    # Current price (use DB value, may be stale)
    current = pos.current_price or pos.entry_price
    entry = pos.entry_price
    trailing = pos.trailing_stop_price
    highest = pos.highest_price
    dynamic_stop = pos.dynamic_stop_pct

    # Calculate distances
    dist = calculate_distance(entry, current, trailing, pos.side)

    # Status indicator
    if trailing and current:
        if pos.side == "Buy":
            is_safe = current > trailing
        else:
            is_safe = current < trailing
        status_icon = "🟢" if is_safe else "🔴"
    else:
        status_icon = "⚪"

    # Dynamic stop status
    dynamic_status = ""
    if dynamic_stop and dist["pnl_pct"]:
        if dist["pnl_pct"] <= float(dynamic_stop):
            dynamic_status = f" ⚠️ DYNAMIC STOP HIT ({dist['pnl_pct']:.2f}% <= {float(dynamic_stop):.2f}%)"
        else:
            dynamic_status = f" 🛡️ Floor: {float(dynamic_stop):.2f}%"

    print(f"\n{'='*60}")
    print(f"{status_icon} {pos.ticker} | {pos.side} | {pos.size} @ {float(entry):.4f}")
    print(f"{'='*60}")
    print(f"  Current Price:    {float(current):.4f}")
    print(f"  Entry Price:      {float(entry):.4f}")
    print(f"  Hours Held:       {hours_held:.1f}h")
    print(f"  Bucket:           {pos.bucket or 'N/A'}")
    print(f"  Regime at Entry:  {pos.regime_at_entry or 'N/A'}")
    print(f"  Signal Source:    {pos.signal_source or 'N/A'}")
    print(f"  Partial Exits:    {pos.partial_exits_taken}")
    print()
    print(f"  Unrealized P&L:   {dist['pnl_pct']:+.2f}% ({dist['pnl_abs']:+.4f} USDT)")
    print()
    print(f"  Trailing Stop:    {float(trailing):.4f}" if trailing else "  Trailing Stop:    ⚪ Not set")
    print(f"  Highest Price:    {float(highest):.4f}" if highest else "  Highest Price:    ⚪ N/A")
    print(f"  Distance to Stop: {dist['to_trailing_pct']:.2f}% ({dist['to_trailing_abs']:.4f})" if trailing else "  Distance to Stop: N/A")
    print(f"  Dynamic Stop %:   {float(dynamic_stop):.2f}%{dynamic_status}" if dynamic_stop else f"  Dynamic Stop %:   ⚪ Not set{dynamic_status}")
    print(f"  Last Mgmt Check:  {pos.last_management_check or 'Never'}")

    # Recent adjustments
    adjustments = await get_recent_trailing_adjustments(pos.id)
    if adjustments:
        print(f"\n  Recent Trailing Adjustments:")
        for adj in adjustments[:3]:
            old_str = f"{float(adj.old_price):.4f}" if adj.old_price else "initial"
            print(f"    • {adj.created_at}: {old_str} → {float(adj.new_price):.4f} ({adj.reason})")


async def main():
    """Main monitoring function."""
    print("\n" + "="*60)
    print("  TRAILING STOP MONITOR - Open Positions")
    print("  " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    print("="*60)

    positions = await get_open_positions()

    if not positions:
        print("\n  No open positions found.")
        return

    print(f"\n  Found {len(positions)} open position(s)")

    # Summary
    total_trailing = sum(1 for p in positions if p.trailing_stop_price)
    total_dynamic = sum(1 for p in positions if p.dynamic_stop_pct)
    print(f"  With Trailing Stop: {total_trailing}/{len(positions)}")
    print(f"  With Dynamic Stop:  {total_dynamic}/{len(positions)}")

    for pos in positions:
        await print_position_status(pos)

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
