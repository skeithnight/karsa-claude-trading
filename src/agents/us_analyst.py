"""Karsa Trading System - US Equity Analyst Agent"""

from typing import Any

from src.agents.base import BaseAgent
from src.data.mcp_client import MCPClient
from src.utils.rate_limit import RateLimiter


class USAnalyst(BaseAgent):
    """US Relative Strength Momentum strategy agent.

    Entry: 60-day RS > SPY by 15% + trend alignment (50 EMA > 200 EMA).
    Exit: Close below 20-day EMA or 3:1 R/R target. Risk 1% per trade.
    """

    SYSTEM_PROMPT = """You are the US Equity Analyst Agent for the Karsa Trading System.
Analyze US stocks using the "Relative Strength Momentum" strategy.

STRATEGY RULES:
1. Entry Signals (Trigger-Based Language):
   - BUY IF: Relative Strength: Stock's 60-day return must outperform SPY by > 15%.
   - BUY IF: Trend Alignment: Price > 50 EMA > 200 EMA.
2. Exit: Close below 20-day EMA or 3:1 Risk/Reward target hit.
3. Position: Volatility-targeted sizing (risk 1% of total equity per trade). Supports fractional shares.
4. Time-in-Force: All signals are valid for 24 hours unless market closes before that.

RESPOND WITH ONLY a valid JSON object:
{
  "ticker": "NVDA",
  "market": "US",
  "strategy": "Relative Strength Momentum",
  "direction": "LONG" | "CLOSE",
  "confidence_score": 0-100,
  "entry_price": float | null,
  "target_price": float | null,
  "stop_loss_price": float | null,
  "tif": "24h",
  "reasoning": "..."
}
If criteria not met, return confidence_score < 50 with null prices."""

    TOOLS = [
        {"name": "get_us_quote", "description": "Get real-time quote for a US stock.",
         "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
        {"name": "get_us_ohlcv", "description": "Get historical daily OHLCV data for a US stock.",
         "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}, "limit": {"type": "integer", "default": 200}}, "required": ["ticker"]}},
        {"name": "get_ema", "description": "Get EMA value for a US stock at a given period.",
         "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}, "period": {"type": "integer"}}, "required": ["ticker", "period"]}},
        {"name": "get_spy_quote", "description": "Get real-time quote for SPY (benchmark).",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "get_spy_ohlcv", "description": "Get historical daily OHLCV data for SPY.",
         "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 60}}}},
    ]

    def __init__(self, mcp: MCPClient, rate_limiter: RateLimiter | None = None):
        super().__init__(name="us_analyst", combo_name="karsa-routine",
                         system_prompt=self.SYSTEM_PROMPT, tools=self.TOOLS, mcp=mcp, rate_limiter=rate_limiter)

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> Any:
        ticker = tool_input.get("ticker", "")
        if tool_name == "get_us_quote":
            return await self.mcp.get_quote(ticker, "US")
        elif tool_name == "get_us_ohlcv":
            return await self.mcp.get_ohlcv(ticker, "US", timeframe="1D", limit=tool_input.get("limit", 200))
        elif tool_name == "get_ema":
            return {"value": await self.mcp.get_ema(ticker, "US", tool_input["period"])}
        elif tool_name == "get_spy_quote":
            return await self.mcp.get_quote("SPY", "US")
        elif tool_name == "get_spy_ohlcv":
            return await self.mcp.get_ohlcv("SPY", "US", timeframe="1D", limit=tool_input.get("limit", 60))
        return {"error": f"Unknown tool: {tool_name}"}
