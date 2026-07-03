"""Tests for StrategySelector — regime-adaptive strategy configuration."""

import pytest

from src.advisory.strategy_selector import (
    STRATEGY_CONFIGS,
    StrategySelector,
)


# ── Constants ──────────────────────────────────────────────────────────────────

REQUIRED_CONFIG_KEYS = {
    "primary_strategy",
    "prompt_modifier",
    "confidence_boost",
    "max_positions",
    "size_multiplier",
    "preferred_pairs",
    "data_focus",
}


class TestStrategyConfigs:
    """Validate the STRATEGY_CONFIGS dict structure."""

    @pytest.mark.parametrize("regime", [
        "FULL_TREND_ALIGNMENT", "MACRO_BULL_MICRO_PULLBACK", "MACRO_BEAR_MICRO_PULLBACK", "MICRO_BREAKOUT_NO_MACRO", "PURE_DEAD_CHOP",
        "SQUEEZE_ALERT", "MICRO_CHOP_IN_MACRO_TREND", "FULL_ALIGNMENT",
        "MICRO_BREAKOUT", "DEAD_CHOP",
    ])
    def test_all_configs_have_required_keys(self, regime):
        config = STRATEGY_CONFIGS[regime]
        assert REQUIRED_CONFIG_KEYS.issubset(config.keys()), (
            f"{regime} missing keys: {REQUIRED_CONFIG_KEYS - config.keys()}"
        )

    def test_full_trend_alignment_values(self):
        cfg = STRATEGY_CONFIGS["FULL_TREND_ALIGNMENT"]
        assert cfg["primary_strategy"] == "Aggressive Trend Continuation"
        assert cfg["confidence_boost"] == 20
        assert cfg["max_positions"] == 8
        assert cfg["size_multiplier"] == 1.0
        assert "BTCUSDT" in cfg["preferred_pairs"]

    def test_macro_bear_micro_pullback_values(self):
        cfg = STRATEGY_CONFIGS["MACRO_BEAR_MICRO_PULLBACK"]
        assert cfg["confidence_boost"] == 10
        assert cfg["max_positions"] == 3
        assert cfg["size_multiplier"] == 0.8

    def test_mean_reversion_values(self):
        cfg = STRATEGY_CONFIGS["MEAN_REVERSION"]
        assert cfg["confidence_boost"] == 0
        assert cfg["max_positions"] == 4
        assert cfg["size_multiplier"] == 0.8

    def test_pure_dead_chop_values(self):
        cfg = STRATEGY_CONFIGS["PURE_DEAD_CHOP"]
        assert cfg["confidence_boost"] == -100
        assert cfg["max_positions"] == 0
        assert cfg["size_multiplier"] == 0.0

    def test_dead_chop_halt(self):
        cfg = STRATEGY_CONFIGS["DEAD_CHOP"]
        assert cfg["primary_strategy"] == "Halt"
        assert cfg["max_positions"] == 0
        assert cfg["size_multiplier"] == 0.0
        assert cfg["confidence_boost"] == -100

    def test_full_alignment_most_aggressive(self):
        cfg = STRATEGY_CONFIGS["FULL_ALIGNMENT"]
        assert cfg["max_positions"] == 8
        assert cfg["size_multiplier"] == 1.5
        assert cfg["confidence_boost"] == 20

    def test_size_multipliers_non_negative(self):
        for regime, cfg in STRATEGY_CONFIGS.items():
            assert cfg["size_multiplier"] >= 0, f"{regime} has negative size_multiplier"

    def test_all_prompts_non_empty(self):
        for regime, cfg in STRATEGY_CONFIGS.items():
            assert len(cfg["prompt_modifier"]) > 20, f"{regime} prompt_modifier too short"


# ── StrategySelector.select() ──────────────────────────────────────────────────

class TestSelect:
    def setup_method(self):
        self.selector = StrategySelector()

    def test_full_trend_alignment(self):
        config = self.selector.select("FULL_TREND_ALIGNMENT")
        assert config["primary_strategy"] == "Aggressive Trend Continuation"
        assert config["confidence_boost"] == 20

    def test_macro_bear_micro_pullback(self):
        config = self.selector.select("MACRO_BEAR_MICRO_PULLBACK")
        assert config["confidence_boost"] == 10
        assert config["size_multiplier"] == 0.8

    def test_mean_reversion(self):
        config = self.selector.select("MEAN_REVERSION")
        assert config["primary_strategy"] == "Mean Reversion"

    def test_pure_dead_chop(self):
        config = self.selector.select("PURE_DEAD_CHOP")
        assert config["primary_strategy"] == "Halt"
        assert config["max_positions"] == 0

    def test_unknown_defaults_to_full_trend_alignment(self):
        config = self.selector.select("UNKNOWN")
        assert config["primary_strategy"] == STRATEGY_CONFIGS["FULL_TREND_ALIGNMENT"]["primary_strategy"]
        assert config["confidence_boost"] == STRATEGY_CONFIGS["FULL_TREND_ALIGNMENT"]["confidence_boost"]
        assert config["size_multiplier"] == STRATEGY_CONFIGS["FULL_TREND_ALIGNMENT"]["size_multiplier"]

    def test_garbage_input_defaults_to_full_trend_alignment(self):
        config = self.selector.select("NOT_A_REAL_REGIME")
        assert config["primary_strategy"] == STRATEGY_CONFIGS["FULL_TREND_ALIGNMENT"]["primary_strategy"]

    def test_squeeze_alert(self):
        config = self.selector.select("SQUEEZE_ALERT")
        assert config["confidence_boost"] == 15

    def test_micro_chop_in_macro_trend(self):
        config = self.selector.select("MICRO_CHOP_IN_MACRO_TREND")
        assert config["size_multiplier"] == 0.8

    def test_full_alignment(self):
        config = self.selector.select("FULL_ALIGNMENT")
        assert config["max_positions"] == 8

    def test_micro_breakout(self):
        config = self.selector.select("MICRO_BREAKOUT")
        assert config["confidence_boost"] == -5

    def test_dead_chop(self):
        config = self.selector.select("DEAD_CHOP")
        assert config["max_positions"] == 0
        assert config["size_multiplier"] == 0.0

    def test_returns_dict_not_reference(self):
        """Each call should return a reference to STRATEGY_CONFIGS, not a copy."""
        config = self.selector.select("FULL_TREND_ALIGNMENT")
        assert config is STRATEGY_CONFIGS["FULL_TREND_ALIGNMENT"]


# ── History tracking ───────────────────────────────────────────────────────────

class TestHistory:
    def setup_method(self):
        self.selector = StrategySelector()

    def test_empty_history_initially(self):
        assert self.selector.get_history() == []

    def test_history_grows_on_each_call(self):
        self.selector.select("FULL_TREND_ALIGNMENT")
        assert len(self.selector.get_history()) == 1

        self.selector.select("PURE_DEAD_CHOP")
        assert len(self.selector.get_history()) == 2

        self.selector.select("MEAN_REVERSION")
        assert len(self.selector.get_history()) == 3

    def test_history_records_regime_and_strategy(self):
        self.selector.select("MACRO_BEAR_MICRO_PULLBACK")
        entry = self.selector.get_history()[0]
        assert entry["regime"] == "MACRO_BEAR_MICRO_PULLBACK"
        assert entry["strategy"] == STRATEGY_CONFIGS["MACRO_BEAR_MICRO_PULLBACK"]["primary_strategy"]

    def test_history_preserves_order(self):
        regimes = ["PURE_DEAD_CHOP", "FULL_TREND_ALIGNMENT", "MEAN_REVERSION", "MACRO_BEAR_MICRO_PULLBACK"]
        for r in regimes:
            self.selector.select(r)

        history = self.selector.get_history()
        assert [h["regime"] for h in history] == regimes

    def test_history_for_unknown_regime(self):
        self.selector.select("UNKNOWN")
        entry = self.selector.get_history()[0]
        assert entry["regime"] == "UNKNOWN"
        assert entry["strategy"] == STRATEGY_CONFIGS["FULL_TREND_ALIGNMENT"]["primary_strategy"]


# ── get_regime_performance ─────────────────────────────────────────────────────

class TestGetRegimePerformance:
    def setup_method(self):
        self.selector = StrategySelector()

    def test_known_regime_returns_all_fields(self):
        result = self.selector.get_regime_performance("FULL_TREND_ALIGNMENT")
        assert result["regime"] == "FULL_TREND_ALIGNMENT"
        assert result["strategy"] == "Aggressive Trend Continuation"
        assert result["size_multiplier"] == 1.0
        assert result["max_positions"] == 8
        assert isinstance(result["preferred_pairs"], list)
        assert "BTCUSDT" in result["preferred_pairs"]

    def test_unknown_regime_defaults_to_full_trend_alignment(self):
        result = self.selector.get_regime_performance("NONEXISTENT")
        assert result["regime"] == "NONEXISTENT"
        assert result["strategy"] == STRATEGY_CONFIGS["FULL_TREND_ALIGNMENT"]["primary_strategy"]
        assert result["size_multiplier"] == STRATEGY_CONFIGS["FULL_TREND_ALIGNMENT"]["size_multiplier"]

    def test_pure_dead_chop_regime(self):
        result = self.selector.get_regime_performance("PURE_DEAD_CHOP")
        assert result["max_positions"] == 0
        assert result["strategy"] == "Halt"

    def test_dead_chop_halt(self):
        result = self.selector.get_regime_performance("DEAD_CHOP")
        assert result["size_multiplier"] == 0.0
        assert result["max_positions"] == 0

    def test_does_not_add_to_history(self):
        """get_regime_performance is a read-only query, should not track history."""
        self.selector.get_regime_performance("FULL_TREND_ALIGNMENT")
        assert len(self.selector.get_history()) == 0

    def test_returns_dict_keys(self):
        result = self.selector.get_regime_performance("MEAN_REVERSION")
        expected_keys = {"regime", "strategy", "size_multiplier", "max_positions", "preferred_pairs"}
        assert set(result.keys()) == expected_keys
