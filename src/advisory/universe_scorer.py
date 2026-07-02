"""Karsa Trading System - Universe Scorer & Ranker

Pure functions for scoring and ranking crypto candidates.
No I/O, no Redis, no LLM — just math.

Scoring weights: volume 40% + momentum 30% + trend 30%.
"""

from src.utils.logging import get_logger

logger = get_logger("universe_scorer")


def score_candidate(candidate: dict) -> float:
    """Score a single candidate on 0-100 scale.

    Expected keys in candidate dict:
        - volume_24h_usd: float  (24h trading volume in USD)
        - price_change_pct: float  (24h price change %)
        - turnover_ratio: float  (volume / open interest, measures activity)
    """
    volume = candidate.get("volume_24h_usd", 0)
    price_change = abs(candidate.get("price_change_pct", 0))
    turnover = candidate.get("turnover_ratio", 0)

    # Volume score (0-40): log scale, $100M+ = 40
    import math
    vol_score = min(40, (math.log10(max(volume, 1)) / 8) * 40)

    # Momentum score (0-30): higher absolute change = more momentum
    # 5%+ change = full score
    mom_score = min(30, (price_change / 5.0) * 30)

    # Trend/turnover score (0-30): higher turnover = more active
    # turnover_ratio > 1.0 = full score
    trend_score = min(30, turnover * 30)

    return round(vol_score + mom_score + trend_score, 2)


def rank_candidates(
    candidates: list[dict],
    top_n: int = 12,
    min_score: float = 20.0,
    always_include: set[str] | None = None,
) -> list[dict]:
    """Rank candidates by score, return top N.

    Args:
        candidates: list of dicts with at least 'symbol' + scoring keys
        top_n: max results
        min_score: minimum score to qualify (unless in always_include)
        always_include: symbols that bypass min_score (e.g. BTCUSDT, ETHUSDT)

    Returns: sorted list of dicts with 'score' key added.
    """
    always_include = always_include or set()

    scored = []
    for c in candidates:
        sym = c.get("symbol", "")
        s = score_candidate(c)
        c_with_score = {**c, "score": s}
        if s >= min_score or sym in always_include:
            scored.append(c_with_score)

    # Sort descending by score
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Ensure always_include symbols are at the front
    forced = [c for c in scored if c.get("symbol") in always_include]
    rest = [c for c in scored if c.get("symbol") not in always_include]

    result = forced + rest
    return result[:top_n]


def filter_liquid(candidates: list[dict], min_volume_usd: float = 5_000_000) -> list[dict]:
    """Pre-filter: remove candidates below minimum volume."""
    return [c for c in candidates if c.get("volume_24h_usd", 0) >= min_volume_usd]
