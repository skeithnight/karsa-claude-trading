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
9. Multi-Timeframe Confirmation: Use 1H candles for entry trigger precision. The 4H confirmation_4h block shows the macro trend — entry direction MUST agree with 4H EMA trend. If 4H trend disagrees, reduce confidence by 20 or skip.
10. Note: The 'rsi_4h' value in the 4H confirmation block is RSI, NOT ADX. Use it as a momentum strength indicator, not a trend strength indicator.

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

_PROFILE_GUIDANCE = {
    "conservative": (
        "RISK PROFILE: CONSERVATIVE — Capital preservation first.\n"
        "- Only recommend trades with very high confidence (>=70).\n"
        "- Require multiple confirming indicators (trend, volume, momentum).\n"
        "- If uncertain, return confidence < 50 (NO TRADE).\n"
        "- Risk/reward ratio must be at least 1:2.\n"
    ),
    "semi_aggressive": (
        "RISK PROFILE: SEMI-AGGRESSIVE — Balanced risk-reward.\n"
        "- Look for trades with moderate-to-high confidence (>=50).\n"
        "- Accept trend continuation setups with solid momentum.\n"
        "- Risk/reward ratio should be at least 1:2.\n"
    ),
    "aggressive": (
        "RISK PROFILE: AGGRESSIVE — Maximize opportunity capture.\n"
        "- Consider trades with lower confidence thresholds (>=35).\n"
        "- Look for early momentum shifts and breakout setups.\n"
        "- Accept higher volatility and wider stops.\n"
        "- IMPORTANT: Do NOT artificially inflate confidence scores. Be honest.\n"
    ),
}

def _build_system_prompt(strategy_config: dict, profile_name: str = "semi_aggressive") -> str:
    """Build dynamic system prompt from strategy config + risk profile."""
    regime_rules = strategy_config.get("prompt_modifier", "")
    strategy_name = strategy_config.get("primary_strategy", "Trend Sentiment Convergence")
    size_mult = strategy_config.get("size_multiplier", 1.0)
    profile_guidance = _PROFILE_GUIDANCE.get(profile_name, _PROFILE_GUIDANCE["semi_aggressive"])

    # Sync base prompt confidence floor with active profile
    min_conf = {"conservative": 70, "semi_aggressive": 50, "aggressive": 35}.get(profile_name, 50)
    base = _BASE_SYSTEM_PROMPT.replace(
        "confidence >= 50",
        f"confidence >= {min_conf}"
    )

    dynamic_section = (
        f"\n\nACTIVE STRATEGY: {strategy_name}\n"
        f"SIZE MULTIPLIER: {size_mult}x\n\n"
        f"{profile_guidance}\n"
        f"REGIME-SPECIFIC RULES:\n{regime_rules}\n"
        "Apply these regime rules in addition to the core rules above. "
        "The regime rules take precedence when there is a conflict."
    )

    return base + dynamic_section

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
        self._profile_name = "semi_aggressive"  # default, updated by orchestrator

        super().__init__(
            name="crypto_analyst",
            combo_name="karsa-routine",
            system_prompt=_build_system_prompt(self._current_config, self._profile_name),
            tools=self.TOOLS,
            mcp=mcp,
            rate_limiter=rate_limiter,
        )
        self._capture_traces = True

    def set_profile(self, profile_name: str):
        """Update risk profile and rebuild system prompt."""
        self._profile_name = profile_name
        self.system_prompt = _build_system_prompt(self._current_config, self._profile_name)

    async def run(self, task: str) -> dict:
        """Override run to inject trade memory context when available."""
        try:
            from src.agents.memory_retriever import get_relevant_trade_memory
            import re
            ticker_match = re.search(r'([A-Z]{3,10})USDT', task)
            ticker = ticker_match.group(1) if ticker_match else ""
            regime = self._current_config.get("primary_strategy", "unknown")
            if ticker:
                memory = await get_relevant_trade_memory(ticker, regime)
                if memory:
                    task = f"{task}\n\n{memory}"
        except Exception:
            pass  # memory is optional
        result = await super().run(task)

        try:
            from src.utils.logging import get_logger
            logger = get_logger("crypto_analyst")
            signals = result if isinstance(result, list) else result.get("signals", [result]) if isinstance(result, dict) else []
            for signal in signals:
                if isinstance(signal, dict) and "ticker" in signal:
                    logger.info("analyst_signal_result",
                        ticker=signal.get("ticker"),
                        direction=signal.get("direction"),
                        confidence=signal.get("confidence_score"),
                        entry_price=signal.get("entry_price"),
                        has_stop_loss=bool(signal.get("stop_loss_price")),
                    )
        except Exception:
            pass

        return result

    def update_strategy(self, regime_state: str, btc_dominance: float | None = None) -> dict:
        """Update agent's strategy based on current regime.

        Call this before scanning to adapt the prompt to market conditions.
        Returns the strategy config that was applied.
        """
        self._current_config = self.strategy_selector.select(regime_state, btc_dominance=btc_dominance)
        self.system_prompt = _build_system_prompt(self._current_config, self._profile_name)

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
            candles = await self.mcp.get_ohlcv(ticker, "CRYPTO", timeframe="1h", limit=tool_input.get("limit", 50))
            if not candles:
                return []
            # Dense CSV: header + rows — saves ~40% tokens vs repeated JSON keys
            header = "ts,o,h,l,c,v"
            rows = [f"{c.get('timestamp','')},{c.get('open',0)},{c.get('high',0)},{c.get('low',0)},{c.get('close',0)},{c.get('volume',0)}" for c in candles]
            return "\n".join([header] + rows)
        elif tool_name == "get_funding_rate":
            return await self.mcp.get_funding_rate(ticker)
        elif tool_name == "get_open_interest":
            return await self.mcp.get_open_interest(ticker)

        # Deterministic TA tools — multi-timeframe: 1H for entry precision, 4H for trend confirmation
        elif tool_name in ("get_crypto_rsi", "get_crypto_bollinger", "get_crypto_macd",
                           "get_crypto_atr", "get_crypto_full_analysis"):
            ohlcv_1h = await self.mcp.get_ohlcv(ticker, "CRYPTO", timeframe="1h", limit=100)
            ohlcv_4h = await self.mcp.get_ohlcv(ticker, "CRYPTO", timeframe="4h", limit=100)
            ohlcv = ohlcv_1h or ohlcv_4h  # fall back to 4H if 1H unavailable
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
                result = full_analysis(ohlcv, ob_imbalance)
                # Add 4H trend confirmation data when both timeframes available
                if ohlcv_1h and ohlcv_4h:
                    full_4h = full_analysis(ohlcv_4h)
                    result["confirmation_4h"] = {
                        "trend_ema": full_4h.get("ema_20", {}).get("ema_20"),
                        "ema_50": full_4h.get("ema_50", {}).get("ema_50"),
                        "rsi_4h": full_4h.get("rsi", {}).get("rsi"),  # proxy for momentum
                    }
                return result

        return {"error": f"Unknown tool: {tool_name}"}
