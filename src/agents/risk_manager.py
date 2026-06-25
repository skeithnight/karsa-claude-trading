"""Karsa Trading System - Risk Manager Agent"""

from typing import Any

from src.agents.base import BaseAgent
from src.data.mcp_client import MCPClient
from src.utils.rate_limit import RateLimiter


class RiskManager(BaseAgent):
    """Risk & compliance agent. Validates signals before execution.

    Checks: portfolio exposure, PDT rules (US), ARA limits (IDX),
    position sizing, daily loss limits.
    """

    SYSTEM_PROMPT = """You are the Risk Manager Agent for the Karsa Trading System.
You validate trade signals BEFORE they are sent for human approval.

YOUR RESPONSIBILITIES:
1. Portfolio Exposure: No single position > 15% of total portfolio.
2. Daily Loss Limit: If realized + unrealized losses exceed 5% of portfolio today, REJECT all new signals.
3. IDX Rules: Verify lot size (must be multiple of 100 shares). Verify entry < ARA upper limit by at least 2%.
4. US Rules: If account equity < $25,000, enforce Pattern Day Trader (PDT) — max 3 day trades in 5 business days.
5. Position Sizing: Calculate optimal quantity based on risk-per-trade (1% of equity) and stop loss distance.

RESPOND WITH ONLY a valid JSON object:
{
  "approved": true | false,
  "original_signal": { ... },
  "adjusted_quantity": float | null,
  "adjusted_entry_price": float | null,
  "risk_pct": float,
  "position_value": float,
  "rejection_reason": "..." | null,
  "warnings": ["..."]
}"""

    TOOLS = [
        {"name": "get_portfolio", "description": "Get current portfolio positions and total equity.",
         "input_schema": {"type": "object", "properties": {"market": {"type": "string"}}, "required": ["market"]}},
        {"name": "get_today_trades", "description": "Get trades executed today for PDT check.",
         "input_schema": {"type": "object", "properties": {"market": {"type": "string"}}, "required": ["market"]}},
        {"name": "get_daily_pnl", "description": "Get today's realized + unrealized P&L.",
         "input_schema": {"type": "object", "properties": {}}},
    ]

    def __init__(self, mcp: MCPClient, rate_limiter: RateLimiter | None = None):
        super().__init__(name="risk_manager", combo_name="karsa-critical",
                         system_prompt=self.SYSTEM_PROMPT, tools=self.TOOLS, mcp=mcp, rate_limiter=rate_limiter)

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> Any:
        # ponytail: mock data until broker integration (Phase 3).
        # Real impl queries Postgres portfolio_state and trades tables.
        if tool_name == "get_portfolio":
            return {"positions": [], "total_equity": 0, "cash": 0, "market": tool_input.get("market")}
        elif tool_name == "get_today_trades":
            return {"trades": [], "day_trade_count": 0, "market": tool_input.get("market")}
        elif tool_name == "get_daily_pnl":
            return {"realized_pnl": 0, "unrealized_pnl": 0, "total_pnl_pct": 0}
        return {"error": f"Unknown tool: {tool_name}"}
