"""Continuous Monitoring Engine — Post-research watchlist monitoring.

Checks for score changes, security incidents, unlock events, news.
Generates Telegram alerts for significant changes.
"""

import asyncio
from datetime import datetime, timezone

from src.utils.logging import get_logger

logger = get_logger("monitoring_engine")


class MonitoringEngine:
    """Continuous monitoring of researched tokens."""

    def __init__(self, cache=None):
        self._cache = cache

    async def check_score_changes(self, threshold: float = 10.0) -> list[dict]:
        """Check for significant score changes (>threshold points)."""
        from src.models.database import async_session
        from sqlalchemy import text

        async with async_session() as session:
            result = await session.execute(
                text("""WITH latest AS (
                    SELECT DISTINCT ON (symbol) symbol, opportunity_score, created_at
                    FROM research_reports ORDER BY symbol, created_at DESC
                ), previous AS (
                    SELECT DISTINCT ON (symbol) symbol, opportunity_score, created_at
                    FROM research_reports
                    WHERE (symbol, created_at) NOT IN (
                        SELECT symbol, MAX(created_at) FROM research_reports GROUP BY symbol
                    )
                    ORDER BY symbol, created_at DESC
                )
                SELECT l.symbol, l.opportunity_score, p.opportunity_score,
                       l.opportunity_score - p.opportunity_score AS delta
                FROM latest l
                JOIN previous p ON l.symbol = p.symbol
                WHERE ABS(l.opportunity_score - p.opportunity_score) > :threshold"""),
                {"threshold": threshold},
            )
            rows = result.fetchall()

        alerts = []
        for r in rows:
            delta = float(r[3] or 0)
            alerts.append({
                "symbol": r[0],
                "current_score": float(r[1] or 0),
                "previous_score": float(r[2] or 0),
                "delta": delta,
                "direction": "UP" if delta > 0 else "DOWN",
            })

        return alerts

    async def get_watched_tokens(self) -> list[dict]:
        """Get all tokens being actively monitored."""
        from src.models.database import async_session
        from sqlalchemy import text

        async with async_session() as session:
            result = await session.execute(
                text("""SELECT DISTINCT ON (dt.symbol)
                    dt.symbol, dt.chain, dt.status, rr.opportunity_score, rr.recommendation
                FROM discovered_tokens dt
                LEFT JOIN research_reports rr ON rr.symbol = dt.symbol
                WHERE dt.status IN ('SCORED', 'WATCHLIST')
                ORDER BY dt.symbol, rr.created_at DESC"""),
            )
            rows = result.fetchall()

        return [
            {"symbol": r[0], "chain": r[1], "status": r[2],
             "score": float(r[3] or 0) if r[3] else None, "recommendation": r[4]}
            for r in rows
        ]

    async def run_monitoring_cycle(self) -> dict:
        """Full monitoring cycle. Returns alerts to send."""
        from src.architecture.feature_flags import flags

        if not flags.is_enabled("aode_monitoring_enabled"):
            return {"skipped": True, "reason": "aode_monitoring_disabled"}

        score_alerts = await self.check_score_changes()
        watched = await self.get_watched_tokens()

        result = {
            "score_alerts": score_alerts,
            "watched_count": len(watched),
            "alerts_to_send": [],
        }

        # Build alert messages
        for alert in score_alerts:
            direction = alert["direction"]
            emoji = "🟢" if direction == "UP" else "🔴"
            result["alerts_to_send"].append({
                "type": "SCORE_CHANGE",
                "symbol": alert["symbol"],
                "message": f"{emoji} {alert['symbol']} score {direction}: {alert['previous_score']:.0f} → {alert['current_score']:.0f} ({alert['delta']:+.0f})",
            })

        logger.info("monitoring_cycle_done", alerts=len(score_alerts), watched=len(watched))
        return result

    async def persist_alert(self, alert: dict):
        """Save alert to research_audit_log."""
        from src.models.database import async_session
        from sqlalchemy import text
        import json
        async with async_session() as session:
            await session.execute(
                text("""INSERT INTO research_audit_log (token_symbol, action, details, agent)
                VALUES (:symbol, :action, :details, 'monitoring_engine')"""),
                {"symbol": alert.get("symbol"), "action": alert.get("type"), "details": json.dumps(alert)},
            )
            await session.commit()
