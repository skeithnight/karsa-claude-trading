"""Karsa Trading System - Regime-Adaptive Strategy Selector

Maps regime → strategy configuration. Pure Python, no LLM calls.
Used by CryptoAnalyst to build dynamic system prompts based on current market regime.
"""

from typing import Any
from src.utils.logging import get_logger

logger = get_logger("strategy_selector")

# Per-regime strategy configurations
STRATEGY_CONFIGS: dict[str, dict[str, Any]] = {
    "FULL_TREND_ALIGNMENT": {
        "primary_strategy": "Aggressive Trend Continuation",
        "prompt_modifier": (
            "REGIME: FULL TREND ALIGNMENT (15m, 4H, 1D ADX all > 25)\n"
            "- Absolute trend perfection across all timeframes.\n"
            "- Ride the momentum. Use slightly wider trailing stops.\n"
            "- Highest confidence (+20). Maximize sizing.\n"
        ),
        "confidence_boost": 20,
        "max_positions": 8,
        "size_multiplier": 1.0,
        "preferred_pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
        "data_focus": ["adx", "volume"],
    },
    "MACRO_BULL_MICRO_PULLBACK": {
        "primary_strategy": "Dip Buying / Accumulation",
        "prompt_modifier": (
            "REGIME: MACRO BULL, MICRO PULLBACK (4H/1D Trending, 15m Resting, Price > 200EMA)\n"
            "- The micro timeframe is chopping/pulling back, but macro trend is fiercely UP.\n"
            "- BUY THE DIP. Look for oversold RSI or lower Bollinger Band touches.\n"
            "- DO NOT short. This is an accumulation zone.\n"
        ),
        "confidence_boost": 10,
        "max_positions": 5,
        "size_multiplier": 0.8,
        "preferred_pairs": ["BTCUSDT", "ETHUSDT"],
        "data_focus": ["rsi", "bollinger", "funding"],
    },
    "MACRO_BEAR_MICRO_PULLBACK": {
        "primary_strategy": "Short the Rally",
        "prompt_modifier": (
            "REGIME: MACRO BEAR, MICRO PULLBACK (4H/1D Trending, 15m Resting, Price < 200EMA)\n"
            "- The micro timeframe is chopping/bouncing, but macro trend is fiercely DOWN.\n"
            "- SHORT THE RALLY. Look for overbought RSI or upper Bollinger Band touches.\n"
            "- DO NOT long. This is a distribution zone.\n"
        ),
        "confidence_boost": 10,
        "max_positions": 3,
        "size_multiplier": 0.8,
        "preferred_pairs": ["BTCUSDT", "ETHUSDT"],
        "data_focus": ["rsi", "bollinger", "funding"],
    },
    "MICRO_BREAKOUT_NO_MACRO": {
        "primary_strategy": "Scalp Breakout",
        "prompt_modifier": (
            "REGIME: MICRO BREAKOUT NO MACRO (15m ADX > 25, 4H/1D ADX < 20)\n"
            "- The 15m is trending, but the macro is dead chop.\n"
            "- Treat this as a quick scalp. Do not expect massive follow-through.\n"
            "- Take profits early and use tight stops.\n"
        ),
        "confidence_boost": -5,
        "max_positions": 3,
        "size_multiplier": 0.5,
        "preferred_pairs": [],
        "data_focus": ["rsi", "volume"],
    },
    "PURE_DEAD_CHOP": {
        "primary_strategy": "Halt",
        "prompt_modifier": (
            "REGIME: PURE DEAD CHOP (No macro or micro trend)\n"
            "- Absolute flatline. Return confidence 0. Do not trade.\n"
        ),
        "confidence_boost": -100,
        "max_positions": 0,
        "size_multiplier": 0.0,
        "preferred_pairs": [],
        "data_focus": [],
    },
    "MEAN_REVERSION": {
        "primary_strategy": "Mean Reversion",
        "prompt_modifier": (
            "REGIME: MEAN-REVERTING MARKET (Hurst < 0.45 — price tends to revert to mean)\n"
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
    "SQUEEZE_ALERT": {
        "primary_strategy": "Breakout Squeeze Play",
        "prompt_modifier": (
            "REGIME: SQUEEZE ALERT (BBW Percentile < 10% + 4H ADX > 20)\n"
            "- Price is coiling tightly while the macro trend is strong.\n"
            "- DO NOT anticipate the breakout. Prepare exact trigger levels at upper/lower bands.\n"
            "- Trade the breakout IMMEDIATELY when price crosses the band with high volume.\n"
            "- High confidence (+15) for breakouts in the direction of the macro trend.\n"
        ),
        "confidence_boost": 15,
        "max_positions": 3,
        "size_multiplier": 1.0,
        "preferred_pairs": [],
        "data_focus": ["bollinger", "volume", "adx"],
    },
    "MICRO_CHOP_IN_MACRO_TREND": {
        "primary_strategy": "Dip Buying / Accumulation",
        "prompt_modifier": (
            "REGIME: MICRO CHOP IN MACRO TREND (15m ADX < 20, 4H/1D ADX > 25)\n"
            "- The micro timeframe is chopping, but the macro trend is fiercely strong.\n"
            "- This is an accumulation zone. Look for mean-reverting dips to 15m lower bands.\n"
            "- DO NOT short the top of the 15m range; the macro trend could explode upwards anytime.\n"
        ),
        "confidence_boost": 5,
        "max_positions": 5,
        "size_multiplier": 0.8,
        "preferred_pairs": [],
        "data_focus": ["rsi", "bollinger", "ema_50"],
    },
    "FULL_ALIGNMENT": {
        "primary_strategy": "Aggressive Trend Continuation",
        "prompt_modifier": (
            "REGIME: FULL ALIGNMENT (15m, 4H, 1D ADX all > 25)\n"
            "- Absolute trend perfection across all timeframes.\n"
            "- Ride the momentum. Use slightly wider trailing stops.\n"
            "- Highest confidence (+20). Maximize sizing.\n"
        ),
        "confidence_boost": 20,
        "max_positions": 8,
        "size_multiplier": 1.5,
        "preferred_pairs": [],
        "data_focus": ["adx", "volume"],
    },
    "MICRO_BREAKOUT": {
        "primary_strategy": "Scalp Breakout",
        "prompt_modifier": (
            "REGIME: MICRO BREAKOUT (15m ADX > 25, 4H/1D ADX < 20)\n"
            "- The 15m is trending, but macro is dead chop.\n"
            "- Treat this as a quick scalp. Do not expect massive follow-through.\n"
            "- Take profits early (1R-1.5R) and use tight stops.\n"
        ),
        "confidence_boost": -5,
        "max_positions": 3,
        "size_multiplier": 0.6,
        "preferred_pairs": [],
        "data_focus": ["rsi", "volume"],
    },
    "DEAD_CHOP": {
        "primary_strategy": "Halt",
        "prompt_modifier": (
            "REGIME: DEAD CHOP (All timeframes < 20 ADX)\n"
            "- Absolute flatline. Return confidence 0. Do not trade.\n"
        ),
        "confidence_boost": -100,
        "max_positions": 0,
        "size_multiplier": 0.0,
        "preferred_pairs": [],
        "data_focus": [],
    }
}


class StrategySelector:
    """Selects strategy configuration based on current market regime."""

    def __init__(self):
        self._history: list[dict] = []

    def select(self, regime_state: str) -> dict[str, Any]:
        """Get strategy config for a regime.

        Args:
            regime_state: One of FULL_TREND_ALIGNMENT, MACRO_BEAR_MICRO_PULLBACK, MEAN_REVERSION, PURE_DEAD_CHOP, UNKNOWN

        Returns:
            Strategy configuration dict. Defaults to FULL_TREND_ALIGNMENT for UNKNOWN.
        """
        config = STRATEGY_CONFIGS.get(regime_state, STRATEGY_CONFIGS["FULL_TREND_ALIGNMENT"])

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
        config = STRATEGY_CONFIGS.get(regime, STRATEGY_CONFIGS["FULL_TREND_ALIGNMENT"])
        return {
            "regime": regime,
            "strategy": config["primary_strategy"],
            "size_multiplier": config["size_multiplier"],
            "max_positions": config["max_positions"],
            "preferred_pairs": config["preferred_pairs"],
        }
