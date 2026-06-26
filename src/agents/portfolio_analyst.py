"""Karsa Trading System - Portfolio Analyst Agent"""

from typing import Any

from src.agents.base import BaseAgent
from src.data.mcp_client import MCPClient
from src.utils.rate_limit import RateLimiter
from src.utils.logging import get_logger

logger = get_logger("portfolio_analyst")


class PortfolioAnalyst(BaseAgent):
    """Investment portfolio analyst — analyzes holdings against live market data.

    Does NOT execute trades. Provides analysis and recommendations only.
    """

    SYSTEM_PROMPT = """You are the Portfolio Analyst for an investment trader.
Your job is to analyze the trader's CURRENT holdings against live market data.

RESPONSIBILITIES:
1. For each holding: compare current price vs avg cost → unrealized P&L %
2. Check technical health: RSI overbought/oversold, BB position, trend (EMA)
3. Flag risks: positions down >10%, overconcentration (>20% in one stock/sector)
4. Suggest actions (Trigger-Based Language):
   - BUY IF: RSI < 30 AND price near lower Bollinger AND cash available.
   - SELL IF: RSI > 70 OR stop loss hit OR position > 20% of portfolio.
   - HOLD IF: Trend intact AND risk acceptable.
5. Portfolio-level: total value, cash ratio, sector/asset allocation
6. Time-in-Force: Recommendations are valid for 24 hours unless market closes before that.

You do NOT execute trades. You provide analysis and recommendations.
The trader makes all decisions.

You will receive a JSON with the trader's portfolio (cash + holdings with qty and avg cost).
For each holding, fetch the current quote and technical indicators using your tools.
Then respond with your analysis.

RESPOND WITH ONLY a valid JSON object:
{
  "portfolio_value": float,
  "cash_pct": float,
  "total_unrealized_pnl_pct": float,
  "holdings": [
    {
      "ticker": str,
      "market": str,
      "qty": float,
      "avg_cost": float,
      "current_price": float,
      "unrealized_pnl_pct": float,
      "technical_health": "bullish" | "neutral" | "bearish",
      "risk_flags": [str],
      "recommendation": "HOLD" | "ADD" | "TRIM" | "CUT",
      "tif": "24h",
      "reasoning": str
    }
  ],
  "portfolio_risks": [str],
  "top_actions": [str]
}"""

    TOOLS = [
        {
            "name": "get_quote",
            "description": "Get current price quote for a stock/ETF.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "market": {"type": "string", "enum": ["IDX", "US", "ETF"]},
                },
                "required": ["ticker", "market"],
            },
        },
        {
            "name": "get_rsi",
            "description": "Get 14-day RSI for a stock/ETF.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "market": {"type": "string", "enum": ["IDX", "US", "ETF"]},
                },
                "required": ["ticker", "market"],
            },
        },
        {
            "name": "get_bollinger",
            "description": "Get 20-day Bollinger Bands for a stock/ETF.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "market": {"type": "string", "enum": ["IDX", "US", "ETF"]},
                },
                "required": ["ticker", "market"],
            },
        },
        {
            "name": "get_ema",
            "description": "Get EMA value at a given period.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "market": {"type": "string", "enum": ["IDX", "US", "ETF"]},
                    "period": {"type": "integer"},
                },
                "required": ["ticker", "market", "period"],
            },
        },
    ]

    def __init__(self, mcp: MCPClient, rate_limiter: RateLimiter | None = None):
        super().__init__(
            name="portfolio_analyst",
            combo_name="karsa-routine",
            system_prompt=self.SYSTEM_PROMPT,
            tools=self.TOOLS,
            mcp=mcp,
            rate_limiter=rate_limiter,
        )

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> Any:
        logger.info("tool_call", agent=self.name, tool=tool_name, input=tool_input)
        ticker = tool_input.get("ticker", "")
        market = tool_input.get("market", "IDX")

        if tool_name == "get_quote":
            return await self.mcp.get_quote(ticker, market)
        elif tool_name == "get_rsi":
            return {"value": await self.mcp.get_rsi(ticker, market)}
        elif tool_name == "get_bollinger":
            return await self.mcp.get_bollinger(ticker, market)
        elif tool_name == "get_ema":
            return {"value": await self.mcp.get_ema(ticker, market, tool_input["period"])}

        return {"error": f"Unknown tool: {tool_name}"}
