"""Portfolio Allocation Engine — Core/Growth/Speculative/Moonshot buckets.

Assigns tokens to investment tiers based on opportunity scores.
Buckets: Core (>80), Growth (60-80), Speculative (40-60), Moonshot (<40).
"""

from src.utils.logging import get_logger

logger = get_logger("portfolio_bucker")

BUCKET_TARGETS = {
    "CORE": 40.0,
    "GROWTH": 30.0,
    "SPECULATIVE": 20.0,
    "MOONSHOT": 10.0,
}

BUCKET_MAX_POSITION_PCT = {
    "CORE": 5.0,
    "GROWTH": 3.0,
    "SPECULATIVE": 1.5,
    "MOONSHOT": 0.5,
}

class PortfolioBucker:
    """Portfolio allocation across investment tiers."""

    def __init__(self, cache=None):
        self._cache = cache

    def classify(self, composite_score: float) -> str:
        """Classify a score into a bucket."""
        if composite_score >= 80:
            return "CORE"
        elif composite_score >= 60:
            return "GROWTH"
        elif composite_score >= 40:
            return "SPECULATIVE"
        else:
            return "MOONSHOT"

    async def get_current_allocation(self) -> dict:
        """Get current bucket allocation from DB."""
        from src.models.database import async_session
        from sqlalchemy import text

        async with async_session() as session:
            result = await session.execute(
                text("""SELECT bucket, target_pct, current_pct, positions, rebalance_needed
                FROM portfolio_allocations ORDER BY bucket"""),
            )
            rows = result.fetchall()

        return {
            r[0]: {
                "target_pct": float(r[1] or 0),
                "current_pct": float(r[2] or 0),
                "positions": r[3],
                "rebalance_needed": r[4],
            }
            for r in rows
        }

    def rebalance_needed(self, current: dict) -> bool:
        """Check if rebalancing is needed (>5% drift from target)."""
        for bucket, target in BUCKET_TARGETS.items():
            current_pct = current.get(bucket, {}).get("current_pct", 0)
            if abs(current_pct - target) > 5:
                return True
        return False
