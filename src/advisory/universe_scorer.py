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
    sector_mapping: dict[str, str] | None = None,
    max_per_sector: int = 2,
    sector_penalty: float = 20.0,
) -> list[dict]:
    """Rank candidates by score dynamically applying sector penalties.

    Args:
        candidates: list of dicts with at least 'symbol' + scoring keys
        top_n: max results
        min_score: minimum score to qualify (unless in always_include)
        always_include: symbols that bypass min_score and penalties
        sector_mapping: mapping of symbol to sector tag
        max_per_sector: max coins allowed from one sector before penalty triggers
        sector_penalty: score deduction when sector limit is reached

    Returns: greedy sorted list of dicts with 'score' key added.
    """
    always_include = always_include or set()
    sector_mapping = sector_mapping or {}

    # Initial scoring
    scored = []
    for c in candidates:
        sym = c.get("symbol", "")
        s = score_candidate(c)
        c_with_score = {**c, "score": s, "base_score": s, "sector": sector_mapping.get(sym, "Unknown")}
        scored.append(c_with_score)

    result = []
    sector_counts = {}

    # 1. Add always_include items first
    remaining = []
    for c in scored:
        if c["symbol"] in always_include:
            result.append(c)
            sect = c["sector"]
            if sect != "Unknown":
                sector_counts[sect] = sector_counts.get(sect, 0) + 1
        else:
            remaining.append(c)

    # 2. Greedily pick the best remaining candidate considering dynamic penalties
    while len(result) < top_n and remaining:
        best_idx = -1
        best_eff_score = -float('inf')

        for i, c in enumerate(remaining):
            sect = c["sector"]
            eff_score = c["base_score"]
            if sect != "Unknown" and sector_counts.get(sect, 0) >= max_per_sector:
                eff_score -= sector_penalty

            if eff_score > best_eff_score:
                best_eff_score = eff_score
                best_idx = i

        if best_idx == -1 or best_eff_score < min_score:
            break  # No more candidates meet the min_score after penalty

        # Pick the best
        best_candidate = remaining.pop(best_idx)
        best_candidate["score"] = best_eff_score  # Update to effective score
        result.append(best_candidate)
        
        sect = best_candidate["sector"]
        if sect != "Unknown":
            sector_counts[sect] = sector_counts.get(sect, 0) + 1

    return result[:top_n]


def filter_liquid(candidates: list[dict], min_volume_usd: float = 5_000_000) -> list[dict]:
    """Pre-filter: remove candidates below minimum volume."""
    return [c for c in candidates if c.get("volume_24h_usd", 0) >= min_volume_usd]
