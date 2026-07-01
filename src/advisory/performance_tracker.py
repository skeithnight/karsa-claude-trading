"""Karsa Trading System - Persistent Performance Tracker

Tracks equity curve, drawdown, and trade statistics over time.
Persists to CryptoPnLSnapshot table for historical analysis.
"""

from datetime import datetime, timedelta, timezone

from src.models.database import async_session
from src.models.tables import CryptoPnLSnapshot, ClosedPaperTrade, CryptoPosition
from src.utils.logging import get_logger
from sqlalchemy import select, func

logger = get_logger("performance_tracker")


class PerformanceTracker:
    """Tracks equity curve and performance metrics persistently."""

    async def get_equity_curve(self, days: int = 30) -> list[dict]:
        """Get equity curve from daily snapshots."""
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        async with async_session() as session:
            result = await session.execute(
                select(CryptoPnLSnapshot)
                .where(CryptoPnLSnapshot.snapshot_date >= cutoff)
                .order_by(CryptoPnLSnapshot.snapshot_date.asc())
            )
            snapshots = result.scalars().all()

        return [
            {
                "date": s.snapshot_date.strftime("%Y-%m-%d"),
                "equity": float(s.equity or 0),
                "realized_pnl": float(s.realized_pnl or 0),
                "unrealized_pnl": float(s.unrealized_pnl or 0),
                "funding_costs": float(s.funding_costs or 0),
                "total_pnl": float(s.total_pnl or 0),
                "open_positions": s.open_positions or 0,
            }
            for s in snapshots
        ]

    async def get_max_drawdown(self, days: int = 30) -> dict:
        """Calculate max drawdown from equity curve."""
        curve = await self.get_equity_curve(days)
        if not curve:
            return {"max_drawdown_pct": 0, "peak_equity": 0, "trough_equity": 0}

        peak = 0
        max_dd = 0
        peak_eq = trough_eq = 0

        for point in curve:
            eq = point["equity"]
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                peak_eq = peak
                trough_eq = eq

        return {
            "max_drawdown_pct": round(max_dd, 2),
            "peak_equity": round(peak_eq, 2),
            "trough_equity": round(trough_eq, 2),
            "data_points": len(curve),
        }

    async def get_cumulative_stats(self, days: int = 30) -> dict:
        """Get cumulative trading statistics."""
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        async with async_session() as session:
            result = await session.execute(
                select(
                    func.sum(ClosedPaperTrade.realized_pnl).label("total_pnl"),
                    func.sum(ClosedPaperTrade.realized_pnl_pct).label("total_pnl_pct"),
                    func.count(ClosedPaperTrade.id).label("trade_count"),
                ).where(
                    ClosedPaperTrade.market == "CRYPTO",
                    ClosedPaperTrade.exit_date >= cutoff,
                )
            )
            stats = result.one()

            pos_result = await session.execute(
                select(func.count(CryptoPosition.id)).where(CryptoPosition.status == "OPEN")
            )
            open_count = pos_result.scalar() or 0

            snap_result = await session.execute(
                select(CryptoPnLSnapshot)
                .order_by(CryptoPnLSnapshot.snapshot_date.desc())
                .limit(1)
            )
            latest_snap = snap_result.scalar_one_or_none()

        return {
            "period_days": days,
            "total_realized_pnl": round(float(stats.total_pnl or 0), 2),
            "total_realized_pnl_pct": round(float(stats.total_pnl_pct or 0), 2),
            "trade_count": stats.trade_count or 0,
            "open_positions": open_count,
            "current_equity": round(float(latest_snap.equity), 2) if latest_snap else 0,
            "last_snapshot": latest_snap.snapshot_date.strftime("%Y-%m-%d %H:%M") if latest_snap else "Never",
        }

    async def get_regime_performance(self, days: int = 30) -> dict:
        """Get performance breakdown by regime."""
        from src.models.tables import CryptoRegimeHistory

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        async with async_session() as session:
            trade_result = await session.execute(
                select(ClosedPaperTrade)
                .where(ClosedPaperTrade.market == "CRYPTO", ClosedPaperTrade.exit_date >= cutoff)
                .order_by(ClosedPaperTrade.exit_date.desc())
            )
            trades = trade_result.scalars().all()

            regime_result = await session.execute(
                select(CryptoRegimeHistory)
                .where(CryptoRegimeHistory.timestamp >= cutoff)
                .order_by(CryptoRegimeHistory.timestamp.desc())
            )
            regimes = regime_result.scalars().all()

        by_regime = {}
        for t in trades:
            if not t.entry_date:
                continue
            entry_dt = t.entry_date
            closest = "UNKNOWN"
            for r in regimes:
                if r.timestamp <= entry_dt:
                    closest = r.regime
                    break

            if closest not in by_regime:
                by_regime[closest] = {"wins": 0, "losses": 0, "pnl": 0, "count": 0}
            by_regime[closest]["count"] += 1
            by_regime[closest]["pnl"] += float(t.realized_pnl_pct or 0)
            if float(t.realized_pnl_pct or 0) > 0:
                by_regime[closest]["wins"] += 1
            else:
                by_regime[closest]["losses"] += 1

        for regime, data in by_regime.items():
            total = data["wins"] + data["losses"]
            data["win_rate"] = round(data["wins"] / total * 100, 1) if total > 0 else 0
            data["pnl"] = round(data["pnl"], 2)

        return by_regime
