"""Decision Engine — fusion brain collecting recommendations from multiple sources."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
import structlog

logger = structlog.get_logger(__name__)


class DecisionAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    IGNORE = "IGNORE"
    REJECT = "REJECT"


@dataclass
class Recommendation:
    source: str
    action: DecisionAction
    confidence: float  # 0.0-1.0
    reason: str = ""


@dataclass
class Decision:
    symbol: str
    action: DecisionAction
    confidence: float
    recommendations: List[Recommendation] = field(default_factory=list)
    explanation: str = ""


class DecisionEngine:
    """Collects recommendations, normalizes, resolves conflicts, produces deterministic output.

    ponytail: weighted average with conflict resolution. No ML.
    """

    def __init__(self):
        self._sources = []

    def add_source(self, source):
        self._sources.append(source)

    async def decide(self, symbol: str, context: dict) -> Decision:
        recs: List[Recommendation] = []
        for src in self._sources:
            rec = await src.recommend(symbol, context)
            if rec:
                recs.append(rec)

        if not recs:
            return Decision(symbol=symbol, action=DecisionAction.IGNORE,
                          confidence=0.0, explanation="No sources provided recommendation")

        # Weighted vote: each source's confidence weights its vote
        action_scores = {}
        for rec in recs:
            action_scores.setdefault(rec.action, 0.0)
            action_scores[rec.action] += rec.confidence

        winner = max(action_scores, key=action_scores.get)
        total_weight = sum(action_scores.values())
        final_confidence = action_scores[winner] / total_weight if total_weight else 0

        explanation = "; ".join(f"{r.source}: {r.reason}" for r in recs if r.reason)

        logger.info("decision_made", symbol=symbol, action=winner.value,
                   confidence=final_confidence, sources=len(recs))

        return Decision(
            symbol=symbol,
            action=winner,
            confidence=final_confidence,
            recommendations=recs,
            explanation=explanation,
        )
