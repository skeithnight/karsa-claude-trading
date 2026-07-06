"""Learning Engine — Outcome tracking and weight recalibration.

Tracks investment outcomes vs initial scores.
Adjusts module weights based on which dimensions best predicted winners.
Extends calibration_engine.py pattern.
"""

from src.utils.logging import get_logger

logger = get_logger("learning_engine")

DEFAULT_WEIGHTS = {
    "fundamental": 0.25, "narrative": 0.15, "smart_money": 0.15,
    "onchain": 0.15, "developer": 0.10, "community": 0.08,
    "market": 0.07, "technical": 0.05,
}

MIN_WEIGHT = 0.03
MAX_WEIGHT = 0.40


class LearningEngine:
    """Tracks outcomes and recalibrates scoring weights."""

    def __init__(self, cache=None):
        self._cache = cache

    async def record_outcome(self, symbol: str, entry_scores: dict, exit_pnl_pct: float,
                              holding_days: int, reason: str = ""):
        """Record a completed investment outcome."""
        from src.models.database import async_session
        from sqlalchemy import text
        import json

        outcome = "WIN" if exit_pnl_pct > 0 else ("LOSS" if exit_pnl_pct < 0 else "BREAKEVEN")

        async with async_session() as session:
            await session.execute(
                text("""INSERT INTO research_audit_log
                    (token_symbol, action, details, agent)
                    VALUES (:symbol, 'outcome_recorded', :details, 'learning_engine')"""),
                {
                    "symbol": symbol,
                    "details": json.dumps({
                        "entry_scores": entry_scores,
                        "exit_pnl_pct": exit_pnl_pct,
                        "holding_days": holding_days,
                        "outcome": outcome,
                        "reason": reason,
                    }),
                },
            )
            await session.commit()

        logger.info("outcome_recorded", symbol=symbol, outcome=outcome, pnl=exit_pnl_pct)

    async def get_accuracy_metrics(self, days: int = 90) -> dict:
        """Analyze prediction accuracy over recent period."""
        from src.models.database import async_session
        from sqlalchemy import text
        import json

        async with async_session() as session:
            result = await session.execute(
                text("""SELECT details FROM research_audit_log
                WHERE action = 'outcome_recorded'
                AND created_at > NOW() - INTERVAL ':days days'
                ORDER BY created_at DESC""").bindparams(days=days),
            )
            rows = result.fetchall()

        if not rows:
            return {"total": 0, "win_rate": 0, "avg_pnl": 0, "module_correlation": {}}

        outcomes = []
        for r in rows:
            try:
                details = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                outcomes.append(details)
            except (json.JSONDecodeError, TypeError):
                continue

        total = len(outcomes)
        wins = sum(1 for o in outcomes if o.get("outcome") == "WIN")
        avg_pnl = sum(o.get("exit_pnl_pct", 0) for o in outcomes) / max(total, 1)

        # Module-level correlation: which scores best predicted winners
        module_correlation = {}
        for module in DEFAULT_WEIGHTS:
            winner_scores = [o["entry_scores"].get(module, 50) for o in outcomes if o.get("outcome") == "WIN"]
            loser_scores = [o["entry_scores"].get(module, 50) for o in outcomes if o.get("outcome") == "LOSS"]
            if winner_scores and loser_scores:
                avg_winner = sum(winner_scores) / len(winner_scores)
                avg_loser = sum(loser_scores) / len(loser_scores)
                # Correlation: positive = module scores higher for winners
                module_correlation[module] = round(avg_winner - avg_loser, 2)

        return {
            "total": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / max(total, 1) * 100, 2),
            "avg_pnl": round(avg_pnl, 4),
            "module_correlation": module_correlation,
        }

    async def recalibrate_weights(self) -> dict:
        """Adjust weights based on module predictive power.

        Modules that correlate with winning trades get higher weight.
        Clamped to [MIN_WEIGHT, MAX_WEIGHT].
        """
        metrics = await self.get_accuracy_metrics()
        correlation = metrics.get("module_correlation", {})

        if not correlation or metrics.get("total", 0) < 10:
            logger.info("recalibrate_skip", reason="insufficient_data", total=metrics.get("total"))
            return DEFAULT_WEIGHTS

        # Start from current weights
        new_weights = dict(DEFAULT_WEIGHTS)

        # Adjust: modules with positive correlation get weight boost
        for module, corr in correlation.items():
            if module not in new_weights:
                continue
            if corr > 5:  # strong positive predictor
                new_weights[module] *= 1.2
            elif corr > 0:  # weak positive
                new_weights[module] *= 1.05
            elif corr < -5:  # strong negative predictor
                new_weights[module] *= 0.7
            elif corr < 0:  # weak negative
                new_weights[module] *= 0.9

        # Clamp
        for module in new_weights:
            new_weights[module] = max(MIN_WEIGHT, min(MAX_WEIGHT, new_weights[module]))

        # Normalize to sum to 1.0
        total_weight = sum(new_weights.values())
        new_weights = {k: round(v / total_weight, 4) for k, v in new_weights.items()}

        # Persist
        await self._save_weights(new_weights)

        logger.info("weights_recalibrated", new_weights=new_weights, metrics=metrics)
        return new_weights

    async def _save_weights(self, weights: dict):
        from src.models.database import async_session
        from sqlalchemy import text
        import json
        async with async_session() as session:
            await session.execute(
                text("""INSERT INTO research_audit_log (token_symbol, action, details, agent)
                VALUES ('SYSTEM', 'weights_updated', :details, 'learning_engine')"""),
                {"details": json.dumps(weights)},
            )
            await session.commit()

    async def generate_post_mortem(self, symbol: str) -> str:
        """Generate a text post-mortem for a completed trade."""
        from src.models.database import async_session
        from sqlalchemy import text
        import json

        async with async_session() as session:
            result = await session.execute(
                text("""SELECT details FROM research_audit_log
                WHERE token_symbol = :symbol AND action = 'outcome_recorded'
                ORDER BY created_at DESC LIMIT 1"""),
                {"symbol": symbol},
            )
            row = result.fetchone()

        if not row:
            return f"No outcome recorded for {symbol}"

        details = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        outcome = details.get("outcome", "UNKNOWN")
        pnl = details.get("exit_pnl_pct", 0)
        entry_scores = details.get("entry_scores", {})

        # Find best and worst scoring modules
        if entry_scores:
            best_module = max(entry_scores, key=entry_scores.get)
            worst_module = min(entry_scores, key=entry_scores.get)
        else:
            best_module = worst_module = "unknown"

        lines = [
            f"Post-Mortem: {symbol}",
            f"Outcome: {outcome} ({pnl:+.2f}%)",
            f"Held: {details.get('holding_days', '?')} days",
            f"Best signal: {best_module} ({entry_scores.get(best_module, 0):.0f})",
            f"Weakest signal: {worst_module} ({entry_scores.get(worst_module, 0):.0f})",
            f"Reason: {details.get('reason', 'N/A')}",
        ]

        if outcome == "LOSS" and entry_scores.get(best_module, 0) > 70:
            lines.append(f"⚠️ High {best_module} score but trade lost — investigate module reliability")

        return "\n".join(lines)
