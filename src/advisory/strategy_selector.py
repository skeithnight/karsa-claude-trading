"""Karsa Trading System - Regime-Adaptive Strategy Selector

Maps regime → strategy configuration. Pure Python, no LLM calls.
Used by CryptoAnalyst to build dynamic system prompts based on current market regime.
"""

from typing import Any
from src.utils.logging import get_logger

logger = get_logger("strategy_selector")

# Per-regime strategy configurations
STRATEGY_CONFIGS: dict[str, dict[str, Any]] = {
    "TREND_BULL": {
        "primary_strategy": "Trend Sentiment Convergence",
        "prompt_modifier": (
            "REGIME: BULLISH TRENDING MARKET (Hurst > 0.5, ADX > 25, BTC > 200 EMA)\n"
            "- FAVOR trend-following entries: Price > 20 EMA > 50 EMA alignment.\n"
            "- Negative funding = contrarian long opportunity (crowds are short in a bull).\n"
            "- Higher confidence for LONG signals (+10 bonus if all 4 conditions align).\n"
            "- SHORT signals require extra confirmation: bearish divergence + positive funding + rising OI.\n"
            "- Allow slightly wider stops (2x ATR) to ride momentum.\n"
            "- Volume spike confirmation is key — new money entering the move.\n"
        ),
        "confidence_boost": 10,
        "max_positions": 6,
        "size_multiplier": 1.2,
        "preferred_pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
        "data_focus": ["funding", "oi", "volume"],
    },
    "TREND_BEAR": {
        "primary_strategy": "Trend Sentiment Convergence",
        "prompt_modifier": (
            "REGIME: BEARISH TRENDING MARKET (Hurst > 0.5, ADX > 25, BTC < 200 EMA)\n"
            "- FAVOR short entries: Price < 20 EMA < 50 EMA alignment.\n"
            "- Positive funding = contrarian short opportunity (crowds are long in a bear).\n"
            "- Reduce overall confidence by 10 points — trends can reverse violently.\n"
            "- Tighter stops (1x ATR) — bear market rallies are sharp and painful.\n"
            "- Avoid counter-trend longs unless extreme oversold (RSI < 25 + volume capitulation).\n"
            "- Watch for short squeeze risk if funding is extremely negative.\n"
        ),
        "confidence_boost": -10,
        "max_positions": 3,
        "size_multiplier": 0.5,
        "preferred_pairs": ["BTCUSDT", "ETHUSDT"],
        "data_focus": ["funding", "liquidation_levels"],
    },
    "MEAN_REVERSION": {
        "primary_strategy": "Mean Reversion",
        "prompt_modifier": (
            "REGIME: MEAN-REVERTING MARKET (Hurst < 0.5 — price tends to revert to mean)\n"
            "- Strategy: Fade extremes. Entry at Bollinger band edges.\n"
            "- BUY when: RSI < 30, close < lower BB, negative funding (oversold + short crowding).\n"
            "- SELL/SHORT when: RSI > 70, close > upper BB, positive funding (overbought + long crowding).\n"
            "- Target: 20-period SMA (mean reversion target).\n"
            "- Stop: Beyond Bollinger band + 1x ATR buffer.\n"
            "- Smaller positions — regime is less predictable than trending.\n"
            "- Avoid if ADX is rising above 25 — transition to trend may be starting.\n"
        ),
        "confidence_boost": 0,
        "max_positions": 4,
        "size_multiplier": 0.8,
        "preferred_pairs": ["ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"],
        "data_focus": ["bollinger", "rsi", "funding"],
    },
    "CHOP": {
        "primary_strategy": "Wait for Breakout",
        "prompt_modifier": (
            "REGIME: CHOPPY / NO CLEAR TREND (ADX < 20 — no directional conviction)\n"
            "- DEFAULT ACTION: Do NOT trade. Return confidence < 50.\n"
            "- Only signal if there is a clear breakout setup: volume > 3x average + price breaks range.\n"
            "- Reduce confidence by 20 points for any signal in this regime.\n"
            "- If forced to trade: very small positions, tight stops (0.75x ATR).\n"
            "- Watch for regime transition: if ADX starts rising above 20, trend may be forming.\n"
            "- Funding extremes (|rate| > 0.1%) in chop = potential squeeze — note but don't trade.\n"
        ),
        "confidence_boost": -20,
        "max_positions": 2,
        "size_multiplier": 0.5,
        "preferred_pairs": ["BTCUSDT", "ETHUSDT"],
        "data_focus": ["adx", "volume", "range"],
    },
}


class StrategySelector:
    """Selects strategy configuration based on current market regime."""

    def __init__(self):
        self._history: list[dict] = []

    def select(self, regime_state: str) -> dict[str, Any]:
        """Get strategy config for a regime.

        Args:
            regime_state: One of TREND_BULL, TREND_BEAR, MEAN_REVERSION, CHOP, UNKNOWN

        Returns:
            Strategy configuration dict. Defaults to TREND_BULL for UNKNOWN.
        """
        config = STRATEGY_CONFIGS.get(regime_state, STRATEGY_CONFIGS["TREND_BULL"])

        self._history.append({
            "regime": regime_state,
            "strategy": config["primary_strategy"],
        })

        logger.debug(
            "strategy_selected",
            regime=regime_state,
            strategy=config["primary_strategy"],
            size_multiplier=config["size_multiplier"],
        )

        return config

    def get_history(self) -> list[dict]:
        """Get strategy selection history for this session."""
        return self._history

    def get_regime_performance(self, regime: str) -> dict:
        """Get human-readable strategy description for a regime."""
        config = STRATEGY_CONFIGS.get(regime, STRATEGY_CONFIGS["TREND_BULL"])
        return {
            "regime": regime,
            "strategy": config["primary_strategy"],
            "size_multiplier": config["size_multiplier"],
            "max_positions": config["max_positions"],
            "preferred_pairs": config["preferred_pairs"],
        }
