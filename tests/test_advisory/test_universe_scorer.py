"""Tests for Universe Scorer — pure scoring and ranking functions."""

import pytest
import math

from src.advisory.universe_scorer import score_candidate, rank_candidates, filter_liquid


# ── score_candidate ────────────────────────────────────────────────────────────

class TestScoreCandidate:
    def test_high_volume_high_momentum_high_turnover(self):
        """Legacy test - adapted to new scoring logic."""
        c = {"volume_24h_usd": 1e9, "price_change_pct": 0.15, "turnover_ratio": 2.0}
        score = score_candidate(c)
        # volume: 100M+ = 30; momentum: 15% 24h = 25 (fallback); no penalty; no squeeze
        # Score should be around 55
        assert 50 <= score <= 60

    def test_zero_inputs(self):
        score = score_candidate({})
        assert score == 0.0

    def test_zero_volume(self):
        """Zero volume returns 0 (below minimum threshold)."""
        c = {"volume_24h_usd": 0, "price_change_pct": 0.10, "turnover_ratio": 1.0}
        score = score_candidate(c)
        assert score == 0.0

    def test_partial_volume_only(self):
        c = {"volume_24h_usd": 1e8}
        score = score_candidate(c)
        # volume: 100M+ = 30; no momentum data; no penalty; no squeeze
        assert score == 30.0

    def test_partial_momentum_only(self):
        c = {"price_change_pct": 0.10}
        score = score_candidate(c)
        # volume 0 (below threshold); returns 0
        assert score == 0.0

    def test_partial_turnover_only(self):
        c = {"turnover_ratio": 1.0}
        score = score_candidate(c)
        # volume 0 (below threshold); returns 0
        assert score == 0.0

    def test_negative_price_change_uses_abs(self):
        c = {"volume_24h_usd": 50_000_000, "price_change_pct": -0.15, "turnover_ratio": 0}
        score = score_candidate(c)
        # volume: 50M+ = 25; momentum: 15% 24h = 25 (fallback); no penalty; no squeeze
        assert 45 <= score <= 55

    def test_small_volume(self):
        """Volume below 250k returns 0."""
        c = {"volume_24h_usd": 1000}
        score = score_candidate(c)
        assert score == 0.0

    def test_max_volume_caps_at_30(self):
        c = {"volume_24h_usd": 1e12}
        score = score_candidate(c)
        # volume score capped at 30
        assert score == 30.0

    def test_momentum_caps_at_40(self):
        c = {"volume_24h_usd": 100_000_000, "price_change_pct": 0.20, "price_change_1h_pct": 0.10}
        score = score_candidate(c)
        # volume: 30; momentum: 40 (early breakout: 10% 1h, 20% 24h < 30%); no penalty; no squeeze
        assert 65 <= score <= 75

    def test_turnover_caps_at_30(self):
        """Turnover is no longer used in new scoring logic."""
        c = {"volume_24h_usd": 100_000_000, "turnover_ratio": 5.0}
        score = score_candidate(c)
        # volume: 30; no momentum data; no penalty; no squeeze
        assert score == 30.0

    def test_realistic_candidate(self):
        c = {"volume_24h_usd": 50_000_000, "price_change_pct": 0.035, "price_change_1h_pct": 0.06, "funding_rate": 0.0001}
        score = score_candidate(c)
        # volume: 50M+ = 25; momentum: 40 (early breakout: 6% 1h, 3.5% 24h < 30%)
        # no penalty (3.5% < 30%); no squeeze (positive funding)
        assert 60 <= score <= 70

    def test_score_range_0_to_100(self):
        """Verify score is always in [0, 100]."""
        test_cases = [
            {"volume_24h_usd": 0, "price_change_pct": 0, "price_change_1h_pct": 0, "funding_rate": 0},
            {"volume_24h_usd": 1e12, "price_change_pct": 10.0, "price_change_1h_pct": 0.50, "funding_rate": -0.001},
            {"volume_24h_usd": 250_000, "price_change_pct": 0.01, "price_change_1h_pct": 0.01, "funding_rate": 0.001},
        ]
        for c in test_cases:
            s = score_candidate(c)
            assert 0 <= s <= 100, f"Score {s} out of range for {c}"

    # ── NEW TESTS: Overextension Penalty ──────────────────────────────────────

    def test_exhausted_pump_is_penalized(self):
        """Simulating EVA (+174% 24h, high volume, normal funding) - should be penalized."""
        c = {
            "volume_24h_usd": 75_000_000,
            "price_change_pct": 1.74,  # 174%
            "price_change_1h_pct": 0.02,  # 2%
            "funding_rate": 0.0001
        }
        score = score_candidate(c)
        # volume: 30; momentum: 25 (24h fallback, 1h too low); penalty: -40 (174% > 80%)
        # Score should be heavily penalized
        assert score < 20, "Exhausted pumps should be heavily penalized!"

    def test_overextension_penalty_tiers(self):
        """Test penalty tiers: 30%, 50%, 80% thresholds."""
        base = {"volume_24h_usd": 100_000_000, "price_change_1h_pct": 0, "funding_rate": 0}

        # 25% 24h - no penalty
        c1 = {**base, "price_change_pct": 0.25}
        s1 = score_candidate(c1)

        # 35% 24h - mild penalty (-10)
        c2 = {**base, "price_change_pct": 0.35}
        s2 = score_candidate(c2)

        # 55% 24h - heavy penalty (-25)
        c3 = {**base, "price_change_pct": 0.55}
        s3 = score_candidate(c3)

        # 85% 24h - severe penalty (-40)
        c4 = {**base, "price_change_pct": 0.85}
        s4 = score_candidate(c4)

        # Verify penalty tiers
        assert s1 > s2 > s3 > s4, "Penalty should increase with 24h change"
        assert s1 - s2 == 10, "Mild penalty should be -10"
        assert s2 - s3 == 15, "Heavy penalty should be -25 (15 more than mild)"
        assert s3 - s4 == 15, "Severe penalty should be -40 (15 more than heavy)"

    # ── NEW TESTS: Early Breakout Detection ───────────────────────────────────

    def test_early_breakout_is_rewarded(self):
        """Simulating EDGE (+20% 24h, but +6% in the last 1h) - should score highly."""
        c = {
            "volume_24h_usd": 22_000_000,
            "price_change_pct": 0.20,  # 20%
            "price_change_1h_pct": 0.06,  # 6%
            "funding_rate": 0.0001
        }
        score = score_candidate(c)
        # volume: 20; momentum: 40 (early breakout: 6% 1h, 20% 24h < 30%)
        # no penalty (20% < 30%); no squeeze (positive funding)
        assert score > 55, "Early breakouts should score highly!"

    def test_early_breakout_with_24h_fallback(self):
        """Test fallback to 24h momentum when 1h data not available."""
        c = {
            "volume_24h_usd": 50_000_000,
            "price_change_pct": 0.15,  # 15%
            "funding_rate": 0
        }
        score = score_candidate(c)
        # volume: 25; momentum: 25 (24h fallback, 15% > 10%)
        # no penalty; no squeeze
        assert 45 <= score <= 55

    # ── NEW TESTS: Short Squeeze Detector ─────────────────────────────────────

    def test_short_squeeze_multiplier(self):
        """Simulating CLO (+15% 24h, +4% 1h, NEGATIVE funding) - should get max bonus."""
        c = {
            "volume_24h_usd": 10_000_000,
            "price_change_pct": 0.15,  # 15%
            "price_change_1h_pct": 0.04,  # 4%
            "funding_rate": -0.0005  # Negative funding
        }
        score = score_candidate(c)
        # volume: 20; momentum: 30 (early breakout: 4% 1h, 15% 24h < 20%)
        # no penalty (15% < 30%); squeeze: 30 (4% 1h, -0.0005 < -0.0001)
        assert score > 70, "Short squeezes should get the maximum bonus!"

    def test_funding_rate_penalties(self):
        """Test extreme positive funding penalty."""
        base = {"volume_24h_usd": 100_000_000, "price_change_pct": 0.10, "price_change_1h_pct": 0}

        # Normal funding
        c1 = {**base, "funding_rate": 0.0001}
        s1 = score_candidate(c1)

        # Extreme positive funding (>0.0005)
        c2 = {**base, "funding_rate": 0.001}
        s2 = score_candidate(c2)

        # Should be penalized by 15 points
        assert s1 - s2 == 15, "Extreme positive funding should be penalized by 15 points"


# ── rank_candidates ────────────────────────────────────────────────────────────

def _make_candidate(symbol, volume=50_000_000, pct=0.03, turnover=1.0):
    return {"symbol": symbol, "volume_24h_usd": volume, "price_change_pct": pct, "turnover_ratio": turnover}


class TestRankCandidates:
    def test_basic_ranking_by_score(self):
        cands = [
            _make_candidate("AAA", volume=1e7, pct=0.02, turnover=0.5),
            _make_candidate("BBB", volume=1e6, pct=0.01, turnover=0.2),
            _make_candidate("CCC", volume=1e9, pct=0.15, turnover=2.0),
        ]
        result = rank_candidates(cands, top_n=3, min_score=0)
        symbols = [c["symbol"] for c in result]
        # CCC highest (55), then AAA (~45), then BBB (~40)
        assert symbols == ["CCC", "AAA", "BBB"]

    def test_top_n_limits_results(self):
        cands = [_make_candidate(f"SYM{i}", volume=1e8, pct=0.05) for i in range(20)]
        result = rank_candidates(cands, top_n=5, min_score=0)
        assert len(result) == 5

    def test_min_score_filters_low(self):
        cands = [
            _make_candidate("HIGH", volume=1e9, pct=0.15, turnover=2.0),
            _make_candidate("LOW", volume=100, pct=0.0001, turnover=0.0),
        ]
        result = rank_candidates(cands, top_n=10, min_score=20.0)
        symbols = [c["symbol"] for c in result]
        assert "HIGH" in symbols
        assert "LOW" not in symbols

    def test_always_include_forces_inclusion(self):
        cands = [
            _make_candidate("MUSTHAVE", volume=100, pct=0, turnover=0),  # score ~0
            _make_candidate("GOOD", volume=1e9, pct=0.15, turnover=2.0),
        ]
        result = rank_candidates(cands, top_n=10, min_score=50, always_include={"MUSTHAVE"})
        symbols = [c["symbol"] for c in result]
        assert "MUSTHAVE" in symbols

    def test_always_include_bypasses_min_score(self):
        cands = [_make_candidate("LOW", volume=1, pct=0, turnover=0)]
        result = rank_candidates(cands, top_n=10, min_score=100, always_include={"LOW"})
        assert len(result) == 1
        assert result[0]["symbol"] == "LOW"

    def test_sector_penalty_reduces_score(self):
        cands = [
            _make_candidate("A1", volume=1e8, pct=0.05, turnover=1.0),
            _make_candidate("A2", volume=1e8, pct=0.05, turnover=1.0),
            _make_candidate("A3", volume=1e8, pct=0.05, turnover=1.0),
            _make_candidate("B1", volume=1e8, pct=0.05, turnover=1.0),
        ]
        sector_map = {"A1": "SECTOR_A", "A2": "SECTOR_A", "A3": "SECTOR_A", "B1": "SECTOR_B"}
        result = rank_candidates(
            cands, top_n=4, min_score=0, sector_mapping=sector_map,
            max_per_sector=2, sector_penalty=20.0
        )
        symbols = [c["symbol"] for c in result]
        # B1 should rank higher than A3 since A3 gets penalized
        assert symbols.index("B1") < symbols.index("A3")

    def test_sector_penalty_does_not_apply_to_unknown(self):
        cands = [
            _make_candidate("X", volume=1e8, pct=0.05, turnover=1.0),
            _make_candidate("Y", volume=1e8, pct=0.05, turnover=1.0),
        ]
        # No sector_mapping → all Unknown → no penalty
        result = rank_candidates(cands, top_n=2, min_score=0)
        assert len(result) == 2
        # Both should have their base score (no penalty applied)
        for c in result:
            assert c["score"] == c["base_score"]

    def test_empty_input(self):
        result = rank_candidates([], top_n=10, min_score=0)
        assert result == []

    def test_single_candidate(self):
        cands = [_make_candidate("SOLO", volume=1e8, pct=0.05, turnover=1.0)]
        result = rank_candidates(cands, top_n=10, min_score=0)
        assert len(result) == 1
        assert result[0]["symbol"] == "SOLO"

    def test_score_key_added(self):
        cands = [_make_candidate("T1", volume=1e8, pct=0.05, turnover=1.0)]
        result = rank_candidates(cands, top_n=1, min_score=0)
        assert "score" in result[0]
        assert "base_score" in result[0]
        assert "sector" in result[0]

    def test_always_include_comes_first(self):
        cands = [
            _make_candidate("FORCED", volume=1, pct=0, turnover=0),
            _make_candidate("BEST", volume=1e9, pct=0.15, turnover=2.0),
        ]
        result = rank_candidates(cands, top_n=10, min_score=0, always_include={"FORCED"})
        assert result[0]["symbol"] == "FORCED"

    def test_always_include_not_in_candidates(self):
        """always_include with symbol not in candidates is a no-op."""
        cands = [_make_candidate("EXISTS", volume=1e8, pct=0.05, turnover=1.0)]
        result = rank_candidates(cands, top_n=10, min_score=0, always_include={"NOPE"})
        assert len(result) == 1
        assert result[0]["symbol"] == "EXISTS"

    def test_all_below_min_score(self):
        cands = [_make_candidate("LOW", volume=1, pct=0, turnover=0)]
        result = rank_candidates(cands, top_n=10, min_score=50)
        assert result == []

    def test_sector_count_increments_correctly(self):
        """After picking 2 from SECTOR_A, the 3rd gets penalized."""
        cands = [
            _make_candidate("A1", volume=1e8, pct=0.05, turnover=1.0),
            _make_candidate("A2", volume=1e8, pct=0.05, turnover=1.0),
            _make_candidate("A3", volume=1e8, pct=0.05, turnover=1.0),
        ]
        sector_map = {"A1": "SECTOR_A", "A2": "SECTOR_A", "A3": "SECTOR_A"}
        result = rank_candidates(
            cands, top_n=3, min_score=0, sector_mapping=sector_map,
            max_per_sector=2, sector_penalty=20.0
        )
        # All 3 should be included (min_score=0), but A3 gets penalty
        assert len(result) == 3
        scores = [c["score"] for c in result]
        # The first two should have higher scores than the third
        assert scores[0] >= scores[2]
        assert scores[1] >= scores[2]


# ── filter_liquid ──────────────────────────────────────────────────────────────

class TestFilterLiquid:
    def test_above_threshold(self):
        cands = [{"symbol": "A", "volume_24h_usd": 10_000_000}]
        result = filter_liquid(cands, min_volume_usd=5_000_000)
        assert len(result) == 1

    def test_below_threshold_filtered(self):
        cands = [{"symbol": "A", "volume_24h_usd": 1_000_000}]
        result = filter_liquid(cands, min_volume_usd=5_000_000)
        assert len(result) == 0

    def test_exact_threshold_included(self):
        cands = [{"symbol": "A", "volume_24h_usd": 5_000_000}]
        result = filter_liquid(cands, min_volume_usd=5_000_000)
        assert len(result) == 1

    def test_empty_input(self):
        result = filter_liquid([], min_volume_usd=5_000_000)
        assert result == []

    def test_zero_threshold_includes_all(self):
        cands = [
            {"symbol": "A", "volume_24h_usd": 0},
            {"symbol": "B", "volume_24h_usd": 100},
        ]
        result = filter_liquid(cands, min_volume_usd=0)
        assert len(result) == 2

    def test_missing_volume_key_defaults_to_zero(self):
        cands = [{"symbol": "NOVOL"}]
        result = filter_liquid(cands, min_volume_usd=1)
        assert len(result) == 0

    def test_mixed_liquidity(self):
        cands = [
            {"symbol": "HIGH", "volume_24h_usd": 100_000_000},
            {"symbol": "MID", "volume_24h_usd": 6_000_000},
            {"symbol": "LOW", "volume_24h_usd": 500_000},
        ]
        result = filter_liquid(cands, min_volume_usd=5_000_000)
        symbols = [c["symbol"] for c in result]
        assert "HIGH" in symbols
        assert "MID" in symbols
        assert "LOW" not in symbols

    def test_preserves_original_dicts(self):
        cands = [{"symbol": "A", "volume_24h_usd": 10_000_000, "extra": "data"}]
        result = filter_liquid(cands, min_volume_usd=5_000_000)
        assert result[0]["extra"] == "data"

    def test_custom_threshold(self):
        cands = [{"symbol": "A", "volume_24h_usd": 250_000}]
        assert len(filter_liquid(cands, min_volume_usd=250_000)) == 1
        assert len(filter_liquid(cands, min_volume_usd=250_001)) == 0
