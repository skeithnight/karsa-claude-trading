"""Karsa Trading System - Crypto Performance Audit (Deterministic)

Queries Signal + ClosedPaperTrade tables to compute trading performance metrics.
Pure Python/SQL — no LLM calls. Returns structured dict for the auditor agent.
"""

from datetime import datetime, timedelta, timezone
from src.models.database import async_session
from src.models.tables import Signal, ClosedPaperTrade
from src.utils.logging import get_logger
from sqlalchemy import select

logger = get_logger("crypto_audit")


class CryptoAuditMetrics:
    """Gather crypto trading performance metrics from the database."""

    async def gather(self, days: int = 7) -> dict:
        """Gather all metrics for the last N days.

        Returns a structured dict ready for LLM consumption.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        async with async_session() as session:
            # --- Closed trades ---
            closed_result = await session.execute(
                select(ClosedPaperTrade)
                .where(
                    ClosedPaperTrade.market == "CRYPTO",
                    ClosedPaperTrade.exit_date >= cutoff,
                )
                .order_by(ClosedPaperTrade.exit_date.desc())
            )
            closed = closed_result.scalars().all()

            # --- All signals ---
            sig_result = await session.execute(
                select(Signal)
                .where(
                    Signal.market == "CRYPTO",
                    Signal.created_at >= cutoff,
                )
                .order_by(Signal.created_at.desc())
            )
            signals = sig_result.scalars().all()

        # --- Compute metrics ---
        trades = []
        for t in closed:
            trades.append({
                "ticker": t.ticker,
                "side": t.side,
                "entry_price": float(t.entry_price or 0),
                "exit_price": float(t.exit_price or 0),
                "pnl_pct": float(t.realized_pnl_pct or 0),
                "pnl_usd": float(t.realized_pnl or 0),
                "exit_reason": t.exit_reason or "N/A",
                "strategy": t.strategy or "N/A",
                "entry_date": t.entry_date.isoformat() if t.entry_date else None,
                "exit_date": t.exit_date.isoformat() if t.exit_date else None,
            })

        sigs = []
        for s in signals:
            sigs.append({
                "ticker": s.ticker,
                "direction": s.direction,
                "confidence": s.confidence_score or 0,
                "status": s.status,
                "reasoning": (s.reasoning or "")[:150],
                "created_at": s.created_at.isoformat() if s.created_at else None,
            })

        total = len(trades)
        winners = [t for t in trades if t["pnl_pct"] > 0]
        losers = [t for t in trades if t["pnl_pct"] <= 0]
        win_rate = (len(winners) / total * 100) if total > 0 else 0

        avg_win = sum(t["pnl_pct"] for t in winners) / len(winners) if winners else 0
        avg_loss = sum(t["pnl_pct"] for t in losers) / len(losers) if losers else 0
        total_pnl = sum(t["pnl_usd"] for t in trades)
        total_pnl_pct = sum(t["pnl_pct"] for t in trades)

        best = max(trades, key=lambda t: t["pnl_pct"]) if trades else None
        worst = min(trades, key=lambda t: t["pnl_pct"]) if trades else None

        # By ticker
        by_ticker = {}
        for t in trades:
            tk = t["ticker"]
            if tk not in by_ticker:
                by_ticker[tk] = {"wins": 0, "losses": 0, "pnl_usd": 0, "pnl_pct": 0}
            if t["pnl_pct"] > 0:
                by_ticker[tk]["wins"] += 1
            else:
                by_ticker[tk]["losses"] += 1
            by_ticker[tk]["pnl_usd"] += t["pnl_usd"]
            by_ticker[tk]["pnl_pct"] += t["pnl_pct"]

        # By direction
        by_direction = {}
        for t in trades:
            d = t["side"]
            if d not in by_direction:
                by_direction[d] = {"wins": 0, "losses": 0, "count": 0}
            by_direction[d]["count"] += 1
            if t["pnl_pct"] > 0:
                by_direction[d]["wins"] += 1
            else:
                by_direction[d]["losses"] += 1

        # Signal stats
        sig_total = len(sigs)
        sig_executed = len([s for s in sigs if s["status"] == "EXECUTED"])
        sig_rejected = len([s for s in sigs if s["status"] == "REJECTED"])
        sig_pending = len([s for s in sigs if s["status"] == "PENDING"])
        avg_confidence = sum(s["confidence"] for s in sigs) / sig_total if sig_total else 0

        # Confidence vs outcome
        high_conf = [s for s in sigs if s["confidence"] >= 70]
        high_conf_tickers = {s["ticker"] for s in high_conf}
        high_conf_wins = len([t for t in trades if t["ticker"] in high_conf_tickers and t["pnl_pct"] > 0])
        high_conf_total = len([t for t in trades if t["ticker"] in high_conf_tickers])

        return {
            "period_days": days,
            "total_trades": total,
            "win_rate": round(win_rate, 1),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "total_pnl_usd": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "best_trade": best,
            "worst_trade": worst,
            "by_ticker": by_ticker,
            "by_direction": by_direction,
            "signals": {
                "total": sig_total,
                "executed": sig_executed,
                "rejected": sig_rejected,
                "pending": sig_pending,
                "avg_confidence": round(avg_confidence, 1),
                "high_confidence_win_rate": round(high_conf_wins / high_conf_total * 100, 1) if high_conf_total else 0,
            },
            "recent_trades": trades[:5],
            "recent_signals": sigs[:10],
        }
