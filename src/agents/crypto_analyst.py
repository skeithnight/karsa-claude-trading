"""Karsa Trading System - Crypto Analyst Agent

Single agent per the existing pattern (like USAnalyst).
Strategy: Trend + Funding Rate + OI convergence.
"""

from typing import Any

from src.agents.base import BaseAgent
from src.data.mcp_client import MCPClient
from src.utils.rate_limit import RateLimiter


class CryptoAnalyst(BaseAgent):
    """Crypto Trend + Sentiment agent.

    Entry: Price > 20 EMA > 50 EMA (trend) + negative funding (contrarian) + rising OI.
    Exit: Close below 20 EMA or 3:1 R/R target. Risk 1% of equity per trade.
    24/7 market — signals valid for 4 hours.
    """

    SYSTEM_PROMPT = """You are the Crypto Analyst Agent for the Karsa Trading System.
Analyze cryptocurrency perpetual contracts using the "Trend + Sentiment Convergence" strategy.

STRATEGY RULES:
1. Entry Signals (ALL must align):
   - Trend: Price > 20 EMA > 50 EMA (bullish alignment)
   - Funding: Negative or near-zero funding rate (crowds are short — contrarian long)
   - Open Interest: Rising OI confirms new money entering the move
   - Volume: Current volume > 1.5x 20-period average (momentum confirmation)
2. Short Entry (inverse — ALL must align):
   - Trend: Price < 20 EMA < 50 EMA
   - Funding: Positive funding rate (crowds are long — contrarian short)
   - OI: Rising OI on the sell side
3. Exit: Close below 20 EMA (for longs) or 3:1 Risk/Reward target hit.
4. Position: Volatility-targeted sizing. Risk 1% of total equity per trade.
5. Time-in-Force: Signals valid for 4 hours (crypto is 24/7).
6. Leverage: Max 3x. Conservative.

IMPORTANT:
- Only generate a signal when confidence >= 50.
- High confidence (70+) requires all 4 conditions aligned.
- If market is in CHOP regime (no clear trend), reduce confidence by 20 points.

RESPOND WITH ONLY a valid JSON object:
{
  "ticker": "BTCUSDT",
  "market": "CRYPTO",
  "strategy": "Trend Sentiment Convergence",
  "direction": "LONG" | "SHORT" | "CLOSE",
  "confidence_score": 0-100,
  "entry_price": float | null,
  "target_price": float | null,
  "stop_loss_price": float | null,
  "tif": "4h",
  "reasoning": "..."
}
If criteria not met, return confidence_score < 50 with null prices."""

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
            "name": "get_ema",
            "description": "Get EMA value for a crypto perpetual at a given period.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "period": {"type": "integer"},
                },
                "required": ["ticker", "period"],
            },
        },
    ]

    def __init__(self, mcp: MCPClient, rate_limiter: RateLimiter | None = None):
        super().__init__(
            name="crypto_analyst",
            combo_name="karsa-routine",
            system_prompt=self.SYSTEM_PROMPT,
            tools=self.TOOLS,
            mcp=mcp,
            rate_limiter=rate_limiter,
        )

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> Any:
        ticker = tool_input.get("ticker", "")
        if tool_name == "get_crypto_quote":
            return await self.mcp.get_quote(ticker, "CRYPTO")
        elif tool_name == "get_crypto_ohlcv":
            return await self.mcp.get_ohlcv(ticker, "CRYPTO", timeframe="1D", limit=tool_input.get("limit", 200))
        elif tool_name == "get_funding_rate":
            return await self.mcp.get_funding_rate(ticker)
        elif tool_name == "get_open_interest":
            return await self.mcp.get_open_interest(ticker)
        elif tool_name == "get_ema":
            return {"value": await self.mcp.get_ema(ticker, "CRYPTO", tool_input["period"])}
        return {"error": f"Unknown tool: {tool_name}"}

    def wipe_memory(self):
        """Clear conversation history — used by /sellall to prevent zombie trades."""
        from src.utils.logging import get_logger
        get_logger("crypto_analyst").info("crypto_memory_wiped", agent=self.name)
