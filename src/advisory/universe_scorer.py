"""Karsa Trading System - Universe Scorer & Ranker

Upgraded scoring logic focusing on early breakouts, penalizing exhaustion,
and rewarding short-squeeze mechanics.

Scoring breakdown:
- Volume Score: 0-30 points (liquidity tiers)
- Early Momentum Score: 0-40 points (1h breakout detection + 24h fallback)
- Overextension Penalty: -40 to -10 points (penalize >30% 24h moves)
- Short Squeeze Detector: 0-30 points (negative funding + price up)
"""

from src.utils.logging import get_logger

logger = get_logger("universe_scorer")


def score_candidate(candidate: dict) -> float:
    """Score a single candidate on 0-100 scale.

    Upgraded scoring logic: Focuses on early breakouts, penalizes exhaustion,
    and rewards short-squeeze mechanics.

    Expected keys in candidate dict:
        - volume_24h_usd: float  (24h trading volume in USD)
        - price_change_pct: float  (24h price change as decimal, e.g., 0.15 = 15%)
        - price_change_1h_pct: float  (1h price change as decimal, e.g., 0.06 = 6%)
        - funding_rate: float  (current funding rate, e.g., -0.0005)
        - turnover_ratio: float  (volume / open interest, measures activity)
    """
    import math

    # Extract data with defaults
    vol_24h = candidate.get("volume_24h_usd", 0)
    price_change_24h = candidate.get("price_change_pct", 0) * 100  # Convert to percentage
    # Use absolute value for 24h change (negative drops are still momentum)
    price_change_24h_abs = abs(price_change_24h)
    price_change_1h = candidate.get("price_change_1h_pct", 0) * 100 if "price_change_1h_pct" in candidate else 0.0
    funding_rate = candidate.get("funding_rate", 0)

    # Base Volume Filter (Hard floor to ensure liquidity)
    # 250K floor: small-cap movers (PYTHUSDT, TUTUSDT etc.) still score.
    # filter_liquid() upstream is the real liquidity gate.
    if vol_24h < 250_000:
        return 0.0

    score = 0.0

    # ==========================================
    # A. VOLUME SCORE (Max 30 points)
    # ==========================================
    if vol_24h >= 100_000_000:
        score += 30
    elif vol_24h >= 50_000_000:
        score += 25
    elif vol_24h >= 10_000_000:
        score += 20
    elif vol_24h >= 2_000_000:
        score += 15
    elif vol_24h >= 500_000:
        score += 10
    else:  # 250K–500K
        score += 5

    # ==========================================
    # B. EARLY MOMENTUM SCORE (Max 40 points)
    # ==========================================
    # 1. The "Early Breakout" Bonus (The real alpha)
    if price_change_1h > 5.0 and price_change_24h_abs < 30.0:
        score += 40  # Catching the start of the move
    elif price_change_1h > 3.0 and price_change_24h_abs < 20.0:
        score += 30
    elif price_change_1h > 1.5:
        score += 20
    # 2. Standard 24h Momentum (Fallback) - use absolute value
    elif price_change_24h_abs > 10.0:
        score += 25
    elif price_change_24h_abs > 5.0:
        score += 15

    # ==========================================
    # C. THE "OVEREXTENSION" PENALTY
    # ==========================================
    if price_change_24h_abs > 80.0:
        score -= 40  # Severe penalty: DO NOT BUY THE TOP
    elif price_change_24h_abs > 50.0:
        score -= 25  # Heavy penalty
    elif price_change_24h_abs > 30.0:
        score -= 10  # Mild penalty

    # ==========================================
    # D. SHORT SQUEEZE DETECTOR (Max 30 points)
    # ==========================================
    if price_change_1h > 2.0 and funding_rate < -0.0001:
        score += 30  # Massive bonus: Short squeeze in progress
    elif price_change_24h_abs > 5.0 and funding_rate < 0:
        score += 15  # Mild bonus

    # Penalize extreme long-funding (overheated longs)
    if funding_rate > 0.0005:
        score -= 15

    return max(0.0, score)


def rank_candidates(
    candidates: list[dict],
    top_n: int = 12,
    min_score: float = 55.0,
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
