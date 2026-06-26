"""Karsa Trading System - IDX Analyst Agent"""

from typing import Any

from src.agents.base import BaseAgent
from src.data.mcp_client import MCPClient
from src.utils.rate_limit import RateLimiter
from src.utils.logging import get_logger

logger = get_logger("idx_analyst")


class IDXAnalyst(BaseAgent):
    """IDX Foreign Flow Breakout strategy agent.

    Entry: 3-day foreign net buy > 5% volume + Bollinger breakout + ARA buffer.
    Exit: +10% target, -5% stop. Lot size: 100 shares.
    """

    SYSTEM_PROMPT = """You are the IDX Analyst Agent for the Karsa Trading System.
Analyze Indonesian stocks for investment opportunities.

STRATEGY RULES:
1. Entry Signals (Trigger-Based Language):
   - BUY IF: Price breaks above the 20-day Bollinger Band upper limit with volume > 1.5x average.
   - BUY IF: Price above 20-day moving average.
2. Exit: Target +10% from entry. Stop loss -5% from entry.
3. Position: Max 15% of portfolio per stock. Minimum 1 lot (100 shares).
4. Time-in-Force: All signals are valid for 24 hours unless market closes before that.

RESPOND WITH ONLY a valid JSON object:
{
  "ticker": "BBCA",
  "market": "IDX",
  "strategy": "Technical Breakout",
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
        {
            "name": "get_idx_quote",
            "description": "Get real-time quote for an IDX stock.",
            "input_schema": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
        {
            "name": "get_idx_ohlcv",
            "description": "Get historical daily OHLCV data for an IDX stock.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["ticker"],
            },
        },
        {
            "name": "get_bollinger",
            "description": "Get 20-day Bollinger Bands for an IDX stock.",
            "input_schema": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
    ]

    def __init__(
        self,
        mcp: MCPClient,
        rate_limiter: RateLimiter | None = None,
    ):
        super().__init__(
            name="idx_analyst",
            combo_name="karsa-routine",
            system_prompt=self.SYSTEM_PROMPT,
            tools=self.TOOLS,
            mcp=mcp,
            rate_limiter=rate_limiter,
        )

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> Any:
        logger.info("tool_call", agent=self.name, tool=tool_name, input=tool_input)
        ticker = tool_input.get("ticker", "")

        if tool_name == "get_idx_quote":
            return await self.mcp.get_quote(ticker, "IDX")
        elif tool_name == "get_idx_ohlcv":
            return await self.mcp.get_ohlcv(
                ticker, "IDX", timeframe="1D", limit=tool_input.get("limit", 20)
            )
        elif tool_name == "get_bollinger":
            return await self.mcp.get_bollinger(ticker, "IDX")

        return {"error": f"Unknown tool: {tool_name}"}
