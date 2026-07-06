"""Policy Engine — centralized, versioned business rules.

Priority: Emergency → Compliance → Operational → Risk → Portfolio → Trading → User
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Callable
import structlog

logger = structlog.get_logger(__name__)


class PolicyCategory(str, Enum):
    EMERGENCY = "emergency"
    COMPLIANCE = "compliance"
    OPERATIONAL = "operational"
    RISK = "risk"
    PORTFOLIO = "portfolio"
    TRADING = "trading"
    USER = "user"


@dataclass
class PolicyResult:
    allowed: bool
    policy_name: str
    reason: str = ""
    category: PolicyCategory = PolicyCategory.TRADING


class Policy:
    """Single policy rule."""

    def __init__(self, name: str, category: PolicyCategory,
                 check: Callable[[dict], PolicyResult], priority: int = 100):
        self.name = name
        self.category = category
        self.check = check
        self.priority = priority

    def evaluate(self, context: dict) -> PolicyResult:
        return self.check(context)


_CATEGORY_ORDER = {
    PolicyCategory.EMERGENCY: 0,
    PolicyCategory.COMPLIANCE: 1,
    PolicyCategory.OPERATIONAL: 2,
    PolicyCategory.RISK: 3,
    PolicyCategory.PORTFOLIO: 4,
    PolicyCategory.TRADING: 5,
    PolicyCategory.USER: 6,
}


class PolicyEngine:
    """Evaluates all policies in priority order. First rejection wins."""

    def __init__(self):
        self._policies: List[Policy] = []

    def add_policy(self, policy: Policy):
        self._policies.append(policy)
        self._policies.sort(key=lambda p: (_CATEGORY_ORDER.get(p.category, 99), p.priority))

    def evaluate(self, context: dict) -> PolicyResult:
        for policy in self._policies:
            result = policy.evaluate(context)
            if not result.allowed:
                logger.info("policy_rejected", policy=policy.name, reason=result.reason)
                return result
        return PolicyResult(allowed=True, policy_name="all", reason="All policies passed")

    def list_policies(self) -> List[dict]:
        return [{"name": p.name, "category": p.category.value, "priority": p.priority}
                for p in self._policies]
