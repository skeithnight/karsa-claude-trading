"""Decision sources — plug-in recommendations."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional
from .engine import Recommendation, DecisionAction


class DecisionSource(ABC):
    """Base interface for decision sources."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def recommend(self, symbol: str, context: dict) -> Optional[Recommendation]: ...


class AnalyzerSource(DecisionSource):
    """Wraps existing analyzer output as a decision source."""
    name = "analyzer"

    async def recommend(self, symbol: str, context: dict) -> Optional[Recommendation]:
        signal = context.get("analyzer_signal")
        if not signal:
            return None
        return Recommendation(
            source=self.name,
            action=DecisionAction(signal.get("direction", "HOLD")),
            confidence=signal.get("confidence", 0.5),
            reason=signal.get("reason", ""),
        )


class PolicySource(DecisionSource):
    """Wraps policy engine output as a decision source."""
    name = "policy"

    async def recommend(self, symbol: str, context: dict) -> Optional[Recommendation]:
        policy_result = context.get("policy_result")
        if not policy_result:
            return None
        if policy_result.get("rejected"):
            return Recommendation(
                source=self.name,
                action=DecisionAction.REJECT,
                confidence=1.0,
                reason=policy_result.get("reason", "Policy rejected"),
            )
        return None
