"""Karsa Trading System - IDX Analyst Agent"""

from typing import Any

from src.agents.base import BaseAgent
from src.data.mcp_client import MCPClient
from src.data.idx_adapter import IDXDataAdapter
from src.utils.rate_limit import RateLimiter


class IDXAnalyst(BaseAgent):
    """IDX Foreign Flow Breakout strategy agent.

    Entry: 3-day foreign net buy > 5% volume + Bollinger breakout + ARA buffer.
    Exit: +10% target, -5% stop. Lot size: 100 shares.
    """

    SYSTEM_PROMPT = """You are the IDX Analyst Agent for the Karsa Trading System.
Analyze Indonesian stocks using the "Foreign Flow Breakout" strategy.

STRATEGY RULES:
1. Entry Signals:
   - Foreign Net Buy: 3+ consecutive days of foreign net buying > 5% of daily volume.
   - Technical: Price breaks above the 20-day Bollinger Band upper limit with volume > 1.5x average.
   - ARA Buffer: Entry price must be at least 2% below the daily Auto Rejection Upper (ARA) limit.
2. Exit: Target +10% from entry. Stop loss -5% from entry. Scale out 50% at +10%.
3. Position: Max 15% of portfolio per stock. Minimum 1 lot (100 shares).

RESPOND WITH ONLY a valid JSON object:
{
  "ticker": "BBCA",
  "market": "IDX",
  "strategy": "Foreign Flow Breakout",
  "direction": "LONG" | "CLOSE",
  "confidence_score": 0-100,
  "entry_price": float | null,
  "target_price": float | null,
  "stop_loss_price": float | null,
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
        {
            "name": "get_foreign_flow",
            "description": "Get recent foreign net buy/sell flow data for an IDX stock.",
            "input_schema": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
        {
            "name": "get_ara_limit",
            "description": "Get Auto Rejection (ARA/ARB) limits for an IDX stock.",
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
        idx_adapter: IDXDataAdapter,
        rate_limiter: RateLimiter | None = None,
    ):
        self.idx_adapter = idx_adapter
        super().__init__(
            name="idx_analyst",
            combo_name="karsa-routine",
            system_prompt=self.SYSTEM_PROMPT,
            tools=self.TOOLS,
            mcp=mcp,
            rate_limiter=rate_limiter,
        )

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> Any:
        ticker = tool_input.get("ticker", "")

        if tool_name == "get_idx_quote":
            return await self.mcp.get_quote(ticker, "IDX")
        elif tool_name == "get_idx_ohlcv":
            return await self.mcp.get_ohlcv(
                ticker, "IDX", timeframe="1D", limit=tool_input.get("limit", 20)
            )
        elif tool_name == "get_bollinger":
            return await self.mcp.get_bollinger(ticker, "IDX")
        elif tool_name == "get_foreign_flow":
            return await self.idx_adapter.get_foreign_flow(ticker)
        elif tool_name == "get_ara_limit":
            return await self.idx_adapter.get_ara_limit(ticker)

        return {"error": f"Unknown tool: {tool_name}"}
