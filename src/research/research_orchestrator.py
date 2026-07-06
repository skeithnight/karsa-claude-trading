"""Research Orchestrator — Pipeline coordinator for AODE.

Runs discovery → filter → score → persist cycle.
Schedules: discovery hourly, research every 4h.
"""

import asyncio
from datetime import datetime, timezone

from src.utils.logging import get_logger

logger = get_logger("research_orchestrator")


class ResearchOrchestrator:
    """Coordinates discovery, research, and scoring pipeline."""

    def __init__(self, cache=None, bybit_client=None):
        self._cache = cache
        self._bybit = bybit_client

    async def run_discovery_cycle(self) -> dict:
        """Full discovery cycle: discover → filter → persist."""
        from src.research.discovery_engine import DiscoveryEngine
        from src.architecture.feature_flags import flags

        if not flags.is_enabled("aode_discovery_enabled"):
            return {"skipped": True, "reason": "aode_discovery_disabled"}

        engine = DiscoveryEngine(cache=self._cache, bybit_client=self._bybit)
        tokens = await engine.discover()
        persisted = await engine.persist_discoveries(tokens)

        logger.info("discovery_cycle_done", discovered=len(tokens), persisted=persisted)
        return {"discovered": len(tokens), "persisted": persisted}

    async def run_research_cycle(self, batch_size: int = 10) -> dict:
        """Research cycle: score top unscored tokens."""
        from src.research.opportunity_scorer import OpportunityScorer
        from src.architecture.feature_flags import flags

        if not flags.is_enabled("aode_research_enabled"):
            return {"skipped": True, "reason": "aode_research_disabled"}

        # Get unscored tokens from DB
        unscored = await self._get_unscored_tokens(batch_size)
        if not unscored:
            return {"scored": 0, "reason": "no_unscored_tokens"}

        scorer = OpportunityScorer(cache=self._cache)
        scored = 0
        for token in unscored:
            try:
                result = await scorer.score_opportunity(
                    symbol=token["symbol"],
                    coingecko_id=token.get("coingecko_id"),
                    contract=token.get("contract_address"),
                    chain=token.get("chain", "ethereum"),
                )
                await scorer.persist_report(result)
                scored += 1

                # Update token status
                await self._update_token_status(token["id"], "SCORED")
            except Exception as e:
                logger.error("research_failed", symbol=token["symbol"], error=str(e))

        logger.info("research_cycle_done", scored=scored, total=len(unscored))
        return {"scored": scored, "total": len(unscored)}

    async def get_top_opportunities(self, n: int = 10) -> list[dict]:
        """Get top N opportunities by composite score."""
        from src.models.database import async_session
        from sqlalchemy import text

        async with async_session() as session:
            result = await session.execute(
                text("""SELECT DISTINCT ON (symbol)
                    symbol, opportunity_score, confidence, risk_category,
                    investment_bucket, recommendation, created_at
                FROM research_reports
                ORDER BY symbol, created_at DESC
                LIMIT :n"""),
                {"n": n},
            )
            rows = result.fetchall()

        return [
            {
                "symbol": r[0], "score": float(r[1] or 0), "confidence": float(r[2] or 0),
                "risk": r[3], "bucket": r[4], "recommendation": r[5],
                "scored_at": r[6].isoformat() if r[6] else None,
            }
            for r in rows
        ]

    async def get_watchlist(self) -> list[dict]:
        """Get tokens with WATCH recommendation."""
        from src.models.database import async_session
        from sqlalchemy import text

        async with async_session() as session:
            result = await session.execute(
                text("""SELECT DISTINCT ON (symbol)
                    symbol, opportunity_score, investment_bucket, recommendation
                FROM research_reports
                WHERE recommendation = 'WATCH'
                ORDER BY symbol, created_at DESC"""),
            )
            rows = result.fetchall()

        return [{"symbol": r[0], "score": float(r[1] or 0), "bucket": r[2], "rec": r[3]} for r in rows]

    async def _get_unscored_tokens(self, limit: int) -> list[dict]:
        """Get tokens that haven't been scored yet."""
        from src.models.database import async_session
        from sqlalchemy import text

        async with async_session() as session:
            result = await session.execute(
                text("""SELECT dt.id, dt.symbol, dt.chain, dt.contract_address
                FROM discovered_tokens dt
                LEFT JOIN research_reports rr ON rr.symbol = dt.symbol
                WHERE dt.status = 'NEW' AND rr.id IS NULL
                ORDER BY dt.discovered_at DESC
                LIMIT :limit"""),
                {"limit": limit},
            )
            rows = result.fetchall()

        return [{"id": r[0], "symbol": r[1], "chain": r[2], "contract_address": r[3]} for r in rows]

    async def _update_token_status(self, token_id: int, status: str):
        from src.models.database import async_session
        from sqlalchemy import text
        async with async_session() as session:
            await session.execute(
                text("UPDATE discovered_tokens SET status = :status, last_updated_at = NOW() WHERE id = :id"),
                {"status": status, "id": token_id},
            )
            await session.commit()
