"""Karsa Trading System - Crypto Analyst Agent

Regime-adaptive agent: strategy and prompt change based on current market regime.
Uses StrategySelector to map regime → strategy config, then builds dynamic prompts.
Uses deterministic TA tools (RSI, BB, MACD, ATR) — LLM calls tools, not raw math.
"""

from typing import Any

from src.agents.base import BaseAgent
from src.advisory.crypto_technicals import calculate_rsi, calculate_bollinger, calculate_ema, calculate_macd, calculate_atr, full_analysis
from src.advisory.strategy_selector import StrategySelector
from src.data.mcp_client import MCPClient
from src.utils.rate_limit import RateLimiter


# Base prompt template — regime-specific rules are injected dynamically
_BASE_SYSTEM_PROMPT = """You are the Crypto Analyst Agent for the Karsa Trading System.
Analyze cryptocurrency perpetual contracts using regime-adaptive strategies.

CORE RULES (always apply):
1. Trend Alignment: Use EMA crossovers (20/50) to confirm direction.
2. Funding Contrarian: Negative funding = crowds are short (contrarian long). Positive = contrarian short.
3. OI Confirmation: Rising OI confirms new money entering the move.
4. Volume: Current volume > 1.5x 20-period average (momentum confirmation).
5. Exit: Close below 20 EMA (for longs) or 3:1 Risk/Reward target hit.
6. Position: Volatility-targeted sizing. Risk 1% of total equity per trade.
7. Time-in-Force: Signals valid for 4 hours (crypto is 24/7).
8. Leverage: Max 3x. Conservative.

IMPORTANT:
- Only generate a signal when confidence >= 50.
- High confidence (70+) requires all 4 conditions aligned.
- You MUST express the "reasoning" field in the voice of a seasoned crypto desk trader: concise, tactical, referencing technical breakouts, funding crowding, and market participant sentiment. Avoid generic lists.
- ALWAYS include the regime name and strategy name in your reasoning.

RESPOND WITH ONLY a valid JSON object:
{{
  "ticker": "BTCUSDT",
  "market": "CRYPTO",
  "strategy": "<strategy name>",
  "direction": "LONG" | "SHORT" | "CLOSE",
  "confidence_score": 0-100,
  "entry_price": float | null,
  "target_price": float | null,
  "stop_loss_price": float | null,
  "tif": "4h",
  "reasoning": "A concise, conviction-filled narrative referencing the current regime, technical setup, funding dynamics, and market participant sentiment."
}}
If criteria not met, return confidence_score < 50 with null prices."""


def _build_system_prompt(strategy_config: dict) -> str:
    """Build dynamic system prompt from strategy config."""
    regime_rules = strategy_config.get("prompt_modifier", "")
    strategy_name = strategy_config.get("primary_strategy", "Trend Sentiment Convergence")
    size_mult = strategy_config.get("size_multiplier", 1.0)

    dynamic_section = (
        f"\n\nACTIVE STRATEGY: {strategy_name}\n"
        f"SIZE MULTIPLIER: {size_mult}x\n\n"
        f"REGIME-SPECIFIC RULES:\n{regime_rules}\n"
        "Apply these regime rules in addition to the core rules above. "
        "The regime rules take precedence when there is a conflict."
    )

    return _BASE_SYSTEM_PROMPT + dynamic_section


class CryptoAnalyst(BaseAgent):
    """Regime-adaptive Crypto Trend + Sentiment agent.

    Strategy and prompt change dynamically based on current market regime.
    Entry/exit rules adapt to BULL, BEAR, MEAN_REVERSION, and CHOP regimes.
    """

    TOOLS = [
        {
            "name": "get_crypto_quote",
            "description": "Get real-time quote for a crypto perpetual (e.g. BTCUSDT). Returns price, volume, bid/ask, funding rate, OI.",
            "input_schema": {
                "type": "object",
                "properties": {"ticker": {"type": "string", "description": "Bybit symbol e.g. BTCUSDT"}},
                "required": ["ticker"],
            },
        },
        {
            "name": "get_crypto_ohlcv",
            "description": "Get historical OHLCV candles for a crypto perpetual.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "limit": {"type": "integer", "default": 200},
                },
                "required": ["ticker"],
            },
        },
        {
            "name": "get_funding_rate",
            "description": "Get current funding rate. Negative = shorts pay longs (bullish signal). Positive = longs pay shorts (bearish signal).",
            "input_schema": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
        {
            "name": "get_open_interest",
            "description": "Get current open interest. Rising OI + rising price = strong trend. Rising OI + falling price = strong sell-off.",
            "input_schema": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
        {
            "name": "get_crypto_rsi",
            "description": "Get RSI (Relative Strength Index). RSI > 70 = overbought, RSI < 30 = oversold. Use for entry timing.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "period": {"type": "integer", "default": 14},
                },
                "required": ["ticker"],
            },
        },
        {
            "name": "get_crypto_bollinger",
            "description": "Get Bollinger Bands. %B > 1 = above upper band (overbought), %B < 0 = below lower (oversold). Bandwidth = volatility.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "period": {"type": "integer", "default": 20},
                },
                "required": ["ticker"],
            },
        },
        {
            "name": "get_crypto_macd",
            "description": "Get MACD. Bullish cross = buy signal, bearish cross = sell signal. Histogram = momentum strength.",
            "input_schema": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
        {
            "name": "get_crypto_atr",
            "description": "Get ATR (Average True Range) for volatility and stop-loss sizing. ATR% > 3 = high volatility.",
            "input_schema": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
        {
            "name": "get_crypto_full_analysis",
            "description": "Get all indicators at once (RSI, Bollinger, EMA, MACD, ATR) plus real-time Orderbook Imbalance (bid/ask volume pressure).",
            "input_schema": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
    ]

    def __init__(self, mcp: MCPClient, rate_limiter: RateLimiter | None = None):
        self.strategy_selector = StrategySelector()
        self._current_config = self.strategy_selector.select("TREND_BULL")  # default

        super().__init__(
            name="crypto_analyst",
            combo_name="karsa-routine",
            system_prompt=_build_system_prompt(self._current_config),
            tools=self.TOOLS,
            mcp=mcp,
            rate_limiter=rate_limiter,
        )
        self._capture_traces = True

    def update_strategy(self, regime_state: str) -> dict:
        """Update agent's strategy based on current regime.

        Call this before scanning to adapt the prompt to market conditions.
        Returns the strategy config that was applied.
        """
        self._current_config = self.strategy_selector.select(regime_state)
        self.system_prompt = _build_system_prompt(self._current_config)

        from src.utils.logging import get_logger
        get_logger("crypto_analyst").info(
            "strategy_updated",
            regime=regime_state,
            strategy=self._current_config["primary_strategy"],
            size_multiplier=self._current_config["size_multiplier"],
        )
        return self._current_config

    @property
    def strategy_config(self) -> dict:
        """Current strategy configuration."""
        return self._current_config

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> Any:
        ticker = tool_input.get("ticker", "")

        # Data tools
        if tool_name == "get_crypto_quote":
            return await self.mcp.get_quote(ticker, "CRYPTO")
        elif tool_name == "get_crypto_ohlcv":
            return await self.mcp.get_ohlcv(ticker, "CRYPTO", timeframe="1D", limit=tool_input.get("limit", 200))
        elif tool_name == "get_funding_rate":
            return await self.mcp.get_funding_rate(ticker)
        elif tool_name == "get_open_interest":
            return await self.mcp.get_open_interest(ticker)

        # Deterministic TA tools — compute from OHLCV, no LLM math
        elif tool_name in ("get_crypto_rsi", "get_crypto_bollinger", "get_crypto_macd",
                           "get_crypto_atr", "get_crypto_full_analysis"):
            ohlcv = await self.mcp.get_ohlcv(ticker, "CRYPTO", timeframe="4h", limit=200)
            if not ohlcv:
                return {"error": f"No OHLCV data for {ticker}"}

            if tool_name == "get_crypto_rsi":
                return calculate_rsi(ohlcv, tool_input.get("period", 14))
            elif tool_name == "get_crypto_bollinger":
                return calculate_bollinger(ohlcv, tool_input.get("period", 20))
            elif tool_name == "get_crypto_macd":
                return calculate_macd(ohlcv)
            elif tool_name == "get_crypto_atr":
                return calculate_atr(ohlcv)
            elif tool_name == "get_crypto_full_analysis":
                # Inject websocket orderbook imbalance metric (Phase 1)
                ob_imbalance = 0.0
                try:
                    bybit = self.mcp._get_bybit()
                    ob_imbalance = await bybit.get_orderbook_imbalance(ticker)
                except Exception:
                    pass
                return full_analysis(ohlcv, ob_imbalance)

        return {"error": f"Unknown tool: {tool_name}"}

    def wipe_memory(self):
        """Clear conversation history — used by /sellall to prevent zombie trades."""
        from src.utils.logging import get_logger
        get_logger("crypto_analyst").info("crypto_memory_wiped", agent=self.name)
