"""Narrative Intelligence — Detect and track crypto narratives.

Deterministic scoring (0-100).
Narratives: AI, DePIN, RWA, Memecoin, Gaming, DeFi, L2, ZK, etc.
"""

import asyncio
from src.utils.logging import get_logger

logger = get_logger("narrative_intel")


class NarrativeIntelligence:
    """Narrative detection and tracking."""

    def __init__(self, cache=None):
        self._cache = cache

    async def _ensure_clients(self):
        pass

    async def close(self):
        """Close all underlying HTTP clients to prevent connection leaks."""
        pass

    async def detect_narratives(self) -> list[dict]:
        """Detect trending narratives — CoinGecko removed, returns empty."""
        return []

        narratives = []
        for cat in categories:
            cat_name = (cat.get("name") or "").lower()
            cat_id = (cat.get("id") or "").lower()

            # Match to known narratives
            matched_narrative = None
            for narrative, keywords in NARRATIVE_MAP.items():
                if any(kw in cat_name or kw in cat_id for kw in keywords):
                    matched_narrative = narrative
                    break

            if not matched_narrative:
                continue

            mcap_change = cat.get("market_cap_change_24h_pct") or 0
            mcap = cat.get("market_cap") or 0

            # Strength: combination of mcap size and recent momentum
            import math
            mcap_score = min(5, (math.log10(max(mcap, 1)) / 10) * 5) if mcap > 0 else 0
            momentum_score = min(5, max(0, mcap_change / 10) * 5)

            strength = round(mcap_score + momentum_score, 2)

            # Momentum direction
            if mcap_change > 5:
                momentum = "increasing"
            elif mcap_change < -5:
                momentum = "decreasing"
            else:
                momentum = "stable"

            narratives.append({
                "narrative": matched_narrative,
                "category_name": cat.get("name"),
                "strength": strength,
                "momentum": momentum,
                "market_cap": mcap,
                "market_cap_change_24h_pct": mcap_change,
                "volume_24h": cat.get("volume_24h"),
                "top_coins": cat.get("top_3_coins", []),
            })

        # Deduplicate: keep highest-strength per narrative
        seen = {}
        for n in narratives:
            key = n["narrative"]
            if key not in seen or n["strength"] > seen[key]["strength"]:
                seen[key] = n

        result = sorted(seen.values(), key=lambda x: x["strength"], reverse=True)
        return result

    def score_narrative(self, narrative: dict) -> float:
        """Score a narrative 0-100 based on strength and momentum."""
        base = narrative.get("strength", 0) * 10  # 0-10 → 0-100
        momentum = narrative.get("momentum", "stable")
        if momentum == "increasing":
            base *= 1.2
        elif momentum == "decreasing":
            base *= 0.7
        return round(min(100, base), 2)

    def map_token_to_narratives(self, token_categories: list[str], detected: list[dict]) -> list[dict]:
        """Map a token's categories to detected narratives."""
        matched = []
        cat_lower = [c.lower() for c in (token_categories or [])]
        for n in detected:
            cat_name = (n.get("category_name") or "").lower()
            if any(cat_name in cl or cl in cat_name for cl in cat_lower):
                matched.append(n)
        return matched

    async def analyze_token(self, symbol: str, coingecko_id: str | None = None) -> dict:
        """Score a token's narrative alignment."""
        await self._ensure_clients()
        narratives = await self.detect_narratives()

        if not coingecko_id:
            return {"symbol": symbol, "score": 50, "narratives": [], "detected": narratives[:5]}

        # CoinGecko removed — no category matching
        matched = []

        if not matched:
            # Not in any trending narrative — neutral score
            return {"symbol": symbol, "score": 30, "narratives": [], "detected": narratives[:5]}

        # Score: best matching narrative score + bonus for multi-narrative
        best_score = max(self.score_narrative(n) for n in matched)
        multi_bonus = min(20, (len(matched) - 1) * 10)
        final_score = min(100, best_score + multi_bonus)

        return {
            "symbol": symbol,
            "score": final_score,
            "narratives": [n["narrative"] for n in matched],
            "detected": narratives[:5],
        }
