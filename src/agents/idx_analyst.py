"""Karsa Trading System - IDX Analyst Agent

Enhanced with sector rotation awareness, foreign flow proxy, earnings blackout logic,
and dynamic ARA/ARB compliance.
"""

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
    Enhanced: sector rotation awareness, flow signals, earnings blackout, circuit breaker.
    """

    SYSTEM_PROMPT = """You are the IDX Analyst Agent for the Karsa Trading System.
Analyze Indonesian stocks for investment opportunities using flow, sector, and technical signals.

STRATEGY RULES:
1. Entry Signals (Trigger-Based Language):
   - BUY IF: Price breaks above the 20-day Bollinger Band upper limit with volume > 1.5x average.
   - BUY IF: Foreign flow signal is BUY or STRONG_BUY (3-day net flow proxy > 3%).
   - BUY IF: Sector rotation signal is LEADING or IMPROVING.
   - BUY IF: Price above 20-day moving average with breadth > 50% advancing.
2. Exit: Target +10% from entry. Stop loss -5% from entry.
3. Position: Max 15% of portfolio per stock. Minimum 1 lot (100 shares).
4. Time-in-Force: All signals are valid for 24 hours unless market closes before that.

IDX-SPECIFIC RULES:
- EARNINGS BLACKOUT: If check_earnings shows days_until <= 5, cap confidence at 30/100.
- CIRCUIT BREAKER: If IHSG circuit breaker is triggered (level 1 or 2), return confidence 0.
- DYNAMIC ARA/ARB: Use get_dynamic_ara_arb to verify entry price is within ARA/ARB bounds.
- LOT SIZE: Always return suggested_lots (1 lot = 100 shares). Use ADV gate for sizing.

RESPOND WITH ONLY a valid JSON object:
{
  "ticker": "BBCA",
  "market": "IDX",
  "strategy": "Flow Breakout",
  "direction": "LONG" | "SHORT" | "CLOSE",
  "confidence_score": 0-100,
  "entry_price": float | null,
  "target_price": float | null,
  "stop_loss_price": float | null,
  "suggested_lots": int | null,
  "tif": "24h",
  "reasoning": "...",
  "flow_signal": "BUY" | "SELL" | "NEUTRAL" | null,
  "sector_rotation": "LEADING" | "LAGGING" | "IMPROVING" | "WEAKENING" | "NEUTRAL" | null,
  "earnings_risk": boolean | null
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
            "name": "get_idx_flow",
            "description": "Get 3-day foreign flow proxy for an IDX stock. Returns net flow %, signal (BUY/SELL/NEUTRAL).",
            "input_schema": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
        {
            "name": "get_idx_breadth",
            "description": "Get IDX market breadth — advancing vs declining stocks, breadth ratio.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "check_earnings",
            "description": "Check if a ticker is near earnings (blackout window). Returns days_until and blackout status.",
            "input_schema": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
        {
            "name": "get_dynamic_ara_arb",
            "description": "Get dynamic ARA/ARB limits for a ticker. Returns ceiling/floor prices.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "prev_close": {"type": "number"},
                },
                "required": ["ticker", "prev_close"],
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
        # Lazy-init intelligence engine (avoids circular import)
        self._intelligence = None

    def _get_intelligence(self):
        if self._intelligence is None:
            from src.advisory.idx_intelligence import IDXMarketIntelligence
            self._intelligence = IDXMarketIntelligence(self.mcp)
        return self._intelligence

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> Any:
        logger.info("tool_call", agent=self.name, tool=tool_name, input=tool_input)
        ticker = tool_input.get("ticker", "")
        intel = self._get_intelligence()

        if tool_name == "get_idx_quote":
            return await self.mcp.get_quote(ticker, "IDX")
        elif tool_name == "get_idx_ohlcv":
            return await self.mcp.get_ohlcv(
                ticker, "IDX", timeframe="1D", limit=tool_input.get("limit", 20)
            )
        elif tool_name == "get_bollinger":
            return await self.mcp.get_bollinger(ticker, "IDX")
        elif tool_name == "get_idx_flow":
            return await intel.flow_tracker.get_ticker_flow(ticker)
        elif tool_name == "get_idx_breadth":
            return await intel.get_breadth_metrics()
        elif tool_name == "check_earnings":
            earnings = intel.earnings.get_earnings(ticker)
            is_blackout = intel.earnings.is_blackout(ticker)
            return {
                "ticker": ticker,
                "earnings": earnings,
                "is_blackout": is_blackout,
                "blackout_cap": 30 if is_blackout else None,
            }
        elif tool_name == "get_dynamic_ara_arb":
            from src.risk.idx_limits import ara_ceiling_dynamic, arb_floor_dynamic
            prev_close = tool_input.get("prev_close", 0)
            return {
                "ticker": ticker,
                "prev_close": prev_close,
                "ara_ceiling": round(ara_ceiling_dynamic(prev_close, ticker), 2),
                "arb_floor": round(arb_floor_dynamic(prev_close, ticker), 2),
            }

        return {"error": f"Unknown tool: {tool_name}"}
