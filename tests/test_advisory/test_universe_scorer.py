"""Tests for Universe Scorer — pure scoring and ranking functions."""

import pytest
import math

from src.advisory.universe_scorer import score_candidate, rank_candidates, filter_liquid


# ── score_candidate ────────────────────────────────────────────────────────────

class TestScoreCandidate:
    def test_high_volume_high_momentum_high_turnover(self):
        c = {"volume_24h_usd": 1e9, "price_change_pct": 10.0, "turnover_ratio": 2.0}
        score = score_candidate(c)
        # volume: log10(1e9)=9 → min(40, 9/8*40)=40; momentum: min(30, 10/5*30)=30; trend: min(30, 2*30)=30
        assert score == 100.0

    def test_zero_inputs(self):
        score = score_candidate({})
        assert score == 0.0

    def test_zero_volume(self):
        c = {"volume_24h_usd": 0, "price_change_pct": 10.0, "turnover_ratio": 1.0}
        score = score_candidate(c)
        # volume: log10(max(0,1))=0 → 0; momentum: 30; trend: 30
        assert score == 60.0

    def test_partial_volume_only(self):
        c = {"volume_24h_usd": 1e8}
        score = score_candidate(c)
        # log10(1e8)=8 → 8/8*40 = 40; momentum 0; trend 0
        assert score == 40.0

    def test_partial_momentum_only(self):
        c = {"price_change_pct": 5.0}
        score = score_candidate(c)
        # volume 0; momentum: 5/5*30=30; trend 0
        assert score == 30.0

    def test_partial_turnover_only(self):
        c = {"turnover_ratio": 1.0}
        score = score_candidate(c)
        # volume 0; momentum 0; trend: 1*30=30
        assert score == 30.0

    def test_negative_price_change_uses_abs(self):
        c = {"volume_24h_usd": 0, "price_change_pct": -10.0, "turnover_ratio": 0}
        score = score_candidate(c)
        # momentum uses abs(-10)=10 → min(30, 10/5*30) = 30
        assert score == 30.0

    def test_small_volume(self):
        c = {"volume_24h_usd": 1000}
        score = score_candidate(c)
        expected_vol = min(40, (math.log10(1000) / 8) * 40)
        assert score == round(expected_vol, 2)

    def test_max_volume_caps_at_40(self):
        c = {"volume_24h_usd": 1e12}
        score = score_candidate(c)
        # volume score capped at 40
        assert score == 40.0

    def test_momentum_caps_at_30(self):
        c = {"price_change_pct": 100.0}
        score = score_candidate(c)
        # 100/5*30 = 600 → capped at 30
        assert score == 30.0

    def test_turnover_caps_at_30(self):
        c = {"turnover_ratio": 5.0}
        score = score_candidate(c)
        # 5*30 = 150 → capped at 30
        assert score == 30.0

    def test_realistic_candidate(self):
        c = {"volume_24h_usd": 50_000_000, "price_change_pct": 3.5, "turnover_ratio": 0.8}
        score = score_candidate(c)
        vol = min(40, (math.log10(50_000_000) / 8) * 40)
        mom = min(30, (3.5 / 5.0) * 30)
        trend = min(30, 0.8 * 30)
        assert score == round(vol + mom + trend, 2)

    def test_score_range_0_to_100(self):
        """Verify score is always in [0, 100]."""
        test_cases = [
            {"volume_24h_usd": 0, "price_change_pct": 0, "turnover_ratio": 0},
            {"volume_24h_usd": 1e12, "price_change_pct": 1000, "turnover_ratio": 100},
            {"volume_24h_usd": 500, "price_change_pct": 0.01, "turnover_ratio": 0.01},
        ]
        for c in test_cases:
            s = score_candidate(c)
            assert 0 <= s <= 100, f"Score {s} out of range for {c}"


# ── rank_candidates ────────────────────────────────────────────────────────────

def _make_candidate(symbol, volume=50_000_000, pct=3.0, turnover=1.0):
    return {"symbol": symbol, "volume_24h_usd": volume, "price_change_pct": pct, "turnover_ratio": turnover}


class TestRankCandidates:
    def test_basic_ranking_by_score(self):
        cands = [
            _make_candidate("AAA", volume=1e7, pct=2.0, turnover=0.5),
            _make_candidate("BBB", volume=1e6, pct=1.0, turnover=0.2),
            _make_candidate("CCC", volume=1e9, pct=10.0, turnover=2.0),
        ]
        result = rank_candidates(cands, top_n=3, min_score=0)
        symbols = [c["symbol"] for c in result]
        # CCC highest (100), then AAA (~47), then BBB (~42)
        assert symbols == ["CCC", "AAA", "BBB"]

    def test_top_n_limits_results(self):
        cands = [_make_candidate(f"SYM{i}", volume=1e8, pct=5.0) for i in range(20)]
        result = rank_candidates(cands, top_n=5, min_score=0)
        assert len(result) == 5

    def test_min_score_filters_low(self):
        cands = [
            _make_candidate("HIGH", volume=1e9, pct=10.0, turnover=2.0),
            _make_candidate("LOW", volume=100, pct=0.01, turnover=0.0),
        ]
        result = rank_candidates(cands, top_n=10, min_score=20.0)
        symbols = [c["symbol"] for c in result]
        assert "HIGH" in symbols
        assert "LOW" not in symbols

    def test_always_include_forces_inclusion(self):
        cands = [
            _make_candidate("MUSTHAVE", volume=100, pct=0, turnover=0),  # score ~0
            _make_candidate("GOOD", volume=1e9, pct=10.0, turnover=2.0),
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
            _make_candidate("A1", volume=1e8, pct=5.0, turnover=1.0),
            _make_candidate("A2", volume=1e8, pct=5.0, turnover=1.0),
            _make_candidate("A3", volume=1e8, pct=5.0, turnover=1.0),
            _make_candidate("B1", volume=1e8, pct=5.0, turnover=1.0),
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
            _make_candidate("X", volume=1e8, pct=5.0, turnover=1.0),
            _make_candidate("Y", volume=1e8, pct=5.0, turnover=1.0),
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
        cands = [_make_candidate("SOLO", volume=1e8, pct=5.0, turnover=1.0)]
        result = rank_candidates(cands, top_n=10, min_score=0)
        assert len(result) == 1
        assert result[0]["symbol"] == "SOLO"

    def test_score_key_added(self):
        cands = [_make_candidate("T1", volume=1e8, pct=5.0, turnover=1.0)]
        result = rank_candidates(cands, top_n=1, min_score=0)
        assert "score" in result[0]
        assert "base_score" in result[0]
        assert "sector" in result[0]

    def test_always_include_comes_first(self):
        cands = [
            _make_candidate("FORCED", volume=1, pct=0, turnover=0),
            _make_candidate("BEST", volume=1e9, pct=10.0, turnover=2.0),
        ]
        result = rank_candidates(cands, top_n=10, min_score=0, always_include={"FORCED"})
        assert result[0]["symbol"] == "FORCED"

    def test_always_include_not_in_candidates(self):
        """always_include with symbol not in candidates is a no-op."""
        cands = [_make_candidate("EXISTS", volume=1e8, pct=5.0, turnover=1.0)]
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
            _make_candidate("A1", volume=1e8, pct=5.0, turnover=1.0),
            _make_candidate("A2", volume=1e8, pct=5.0, turnover=1.0),
            _make_candidate("A3", volume=1e8, pct=5.0, turnover=1.0),
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
