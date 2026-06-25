"""Karsa Trading System - ETF Analyst Agent"""

from typing import Any

from src.agents.base import BaseAgent
from src.data.mcp_client import MCPClient
from src.utils.rate_limit import RateLimiter


class ETFAnalyst(BaseAgent):
    """ETF Macro Trend & Mean Reversion strategy agent.

    Trend: Price > 200 SMA (broad ETFs).
    Mean Reversion: RSI < 30 + lower Bollinger touch (sector ETFs).
    """

    SYSTEM_PROMPT = """You are the ETF Analyst Agent for the Karsa Trading System.
Analyze ETFs using the "Macro Trend & Mean Reversion" strategies.

STRATEGY RULES:
1. Strategy A (Trend Following - for SPY, QQQ, ETFIDX):
   - Entry: Buy when price > 200 SMA.
   - Rebalance monthly.
2. Strategy B (Mean Reversion - for Sector ETFs like XLF, XLK):
   - Entry: Buy when RSI(14) < 30 AND price touches lower Bollinger Band.
   - Exit: Sell when RSI(14) > 70.

RESPOND WITH ONLY a valid JSON object:
{
  "ticker": "XLF",
  "market": "ETF",
  "strategy": "Macro Trend & Mean Reversion",
  "direction": "LONG" | "CLOSE",
  "confidence_score": 0-100,
  "entry_price": float | null,
  "target_price": float | null,
  "stop_loss_price": float | null,
  "reasoning": "..."
}
If criteria not met, return confidence_score < 50 with null prices."""

    TOOLS = [
        {"name": "get_etf_quote", "description": "Get real-time quote for an ETF.",
         "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
        {"name": "get_etf_ohlcv", "description": "Get historical daily OHLCV data for an ETF.",
         "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}, "limit": {"type": "integer", "default": 200}}, "required": ["ticker"]}},
        {"name": "get_rsi", "description": "Get 14-day RSI for an ETF.",
         "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
        {"name": "get_bollinger", "description": "Get 20-day Bollinger Bands for an ETF.",
         "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
    ]

    def __init__(self, mcp: MCPClient, rate_limiter: RateLimiter | None = None):
        super().__init__(name="etf_analyst", combo_name="karsa-routine",
                         system_prompt=self.SYSTEM_PROMPT, tools=self.TOOLS, mcp=mcp, rate_limiter=rate_limiter)

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> Any:
        ticker = tool_input.get("ticker", "")
        if tool_name == "get_etf_quote":
            return await self.mcp.get_quote(ticker, "ETF")
        elif tool_name == "get_etf_ohlcv":
            return await self.mcp.get_ohlcv(ticker, "ETF", timeframe="1D", limit=tool_input.get("limit", 200))
        elif tool_name == "get_rsi":
            return {"value": await self.mcp.get_rsi(ticker, "ETF")}
        elif tool_name == "get_bollinger":
            return await self.mcp.get_bollinger(ticker, "ETF")
        return {"error": f"Unknown tool: {tool_name}"}
