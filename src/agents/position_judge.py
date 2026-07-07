"""Karsa Trading System — Position Judge Agent

AI-powered hold/fold decision engine for the Performance Gate.
Two-tier evaluation:

  Tier 1 (cheap): Compact prompt, no tools, position metadata only.
    → Fast decision on ambiguous positions.
  Tier 2 (escalated): Full tool access (price action, volume, regime).
    → Triggered when Tier 1 says HOLD but position still underperforms.

Flow:
  PerformanceGate.evaluate() returns AMBIGUOUS zone →
  PositionJudge.cheap_pass(position_data) →
    if HOLD and still bad at next checkpoint →
  PositionJudge.escalated_pass(position_data) →
    if still bad → EXIT (final)

Output schema:
  {"action": "HOLD"|"EXIT"|"TIGHTEN_STOP", "confidence": 0-100,
   "reason": "...", "new_stop_pct": float|null}
"""

import json
import re
import time
from typing import Any

from src.agents.base import BaseAgent
from src.data.mcp_client import MCPClient
from src.utils.logging import get_logger
from src.utils.rate_limit import RateLimiter
from src.metrics.crypto_metrics import (
    record_ai_decision,
    record_tier_used,
    record_escalation,
    record_confidence_score,
    record_judge_latency,
)

logger = get_logger("position_judge")


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_CHEAP_SYSTEM_PROMPT = """You are the Position Lifecycle Judge for Karsa Trading System.
A position has been flagged as AMBIGUOUS by the mechanical performance gate.
Your job: decide whether to HOLD, EXIT, or TIGHTEN_STOP.

CONTEXT PROVIDED:
- Position: ticker, side, entry price, current price, gain %, hours held
- Bucket: meme/standard/core (meme = aggressive timeline, core = patient)
- Gate reason: why it was flagged

DECISION RULES:
1. If gain is negative and trending down → EXIT. Don't hope.
2. If gain is flat but position is young (<2h for meme, <12h for standard) → HOLD.
3. If gain is slightly positive but below checkpoint minimum → HOLD if momentum, EXIT if flat.
4. Meme bucket: be aggressive on exits. Dead money = lost opportunity.
5. Core bucket: be patient. Give it time unless bleeding badly.

RESPOND WITH ONLY a valid JSON object:
{{
  "action": "HOLD" | "EXIT" | "TIGHTEN_STOP",
  "confidence": 0-100,
  "reason": "One-line tactical reason referencing the position's actual numbers."
}}"""


_ESCALATED_SYSTEM_PROMPT = """You are the Position Lifecycle Judge — ESCALATED REVIEW.
The cheap judge previously said HOLD, but the position is STILL underperforming.
You have access to full market data tools. Use them before deciding.

This is a second opinion. Be more skeptical than the first judge.
If in doubt → EXIT. Capital trapped in a dead position can't find a winner.

RULES:
1. Call get_price_action to check if price is consolidating or bleeding.
2. Call get_volume_profile to check if interest is dying.
3. Call get_market_regime to check if macro is hostile.
4. If all three look bad → EXIT with high confidence.
5. If price consolidating near entry with decent volume → TIGHTEN_STOP.
6. Only HOLD if you have concrete evidence it will recover.

RESPOND WITH ONLY a valid JSON object:
{{
  "action": "HOLD" | "EXIT" | "TIGHTEN_STOP",
  "confidence": 0-100,
  "reason": "Tactical reason referencing actual data from tools.",
  "new_stop_pct": float | null
}}"""


# ---------------------------------------------------------------------------
# Tools — only used in escalated pass
# ---------------------------------------------------------------------------

ESCALATED_TOOLS = [
    {
        "name": "get_price_action",
        "description": "Get recent OHLCV candles for a crypto perpetual. Use to check if price is consolidating, trending, or bleeding.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Bybit symbol e.g. BTCUSDT"},
                "interval": {"type": "string", "default": "5m", "description": "Candle interval: 1m, 5m, 15m, 1h"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_volume_profile",
        "description": "Get buy/sell volume ratio and recent volume trend. Declining volume = dying interest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_market_regime",
        "description": "Get current BTC/ETH market regime. Regime name, ADX, Hurst exponent, BTC dominance.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ---------------------------------------------------------------------------
# PositionJudge agent
# ---------------------------------------------------------------------------

class PositionJudge(BaseAgent):
    """AI-powered position lifecycle judge.

    Two-tier evaluation:
    - cheap_pass(): no tools, compact prompt, fast decision
    - escalated_pass(): full tools, skeptical prompt, deep analysis

    Both return standardized judgment dict.
    """

    def __init__(self, mcp: MCPClient, rate_limiter: RateLimiter | None = None):
        super().__init__(
            name="position_judge",
            combo_name="karsa-routine",
            system_prompt=_CHEAP_SYSTEM_PROMPT,
            tools=[],  # no tools for cheap pass
            mcp=mcp,
            rate_limiter=rate_limiter,
            max_iterations=5,
        )

    async def cheap_pass(self, position_data: dict) -> dict:
        """Tier 1: Fast judgment with no tool access.

        Args:
            position_data: dict with ticker, side, entry_price, current_price,
                          gain_pct, hours_held, bucket, gate_reason, checkpoint_minutes

        Returns:
            {"action": "HOLD"|"EXIT"|"TIGHTEN_STOP", "confidence": int,
             "reason": str, "new_stop_pct": float|None}
        """
        self.system_prompt = _CHEAP_SYSTEM_PROMPT
        self.tools = []

        # Track tier usage and latency
        record_tier_used("cheap")
        start_time = time.time()

        task = self._build_task(position_data, escalated=False)
        result = await self.run(task)
        judgment = self._normalize_result(result, position_data)

        # Record metrics
        latency = time.time() - start_time
        record_judge_latency("cheap", latency)
        record_ai_decision(judgment["action"])
        record_confidence_score(judgment["confidence"])

        return judgment

    async def escalated_pass(self, position_data: dict) -> dict:
        """Tier 2: Full analysis with market data tools.

        Args:
            position_data: same as cheap_pass, plus optional prior_judgment

        Returns:
            Same schema as cheap_pass.
        """
        self.system_prompt = _ESCALATED_SYSTEM_PROMPT
        self.tools = ESCALATED_TOOLS

        # Track tier usage, escalation, and latency
        record_tier_used("escalated")
        record_escalation()
        start_time = time.time()

        task = self._build_task(position_data, escalated=True)
        result = await self.run(task)
        judgment = self._normalize_result(result, position_data)

        # Record metrics
        latency = time.time() - start_time
        record_judge_latency("escalated", latency)
        record_ai_decision(judgment["action"])
        record_confidence_score(judgment["confidence"])

        return judgment

    # ------------------------------------------------------------------
    # Tool dispatch (escalated pass only)
    # ------------------------------------------------------------------

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> Any:
        """Dispatch tool calls to MCP client."""
        ticker = tool_input.get("ticker", "")

        if tool_name == "get_price_action":
            interval = tool_input.get("interval", "5m")
            limit = tool_input.get("limit", 50)
            ohlcv = await self.mcp.get_ohlcv(ticker, "CRYPTO", timeframe=interval, limit=limit)
            if not ohlcv:
                return {"error": f"No OHLCV data for {ticker}"}
            # Phase 3B: Summarize OHLCV instead of returning raw candles
            recent = ohlcv[-20:]
            closes = [float(c["close"]) for c in recent]
            highs = [float(c["high"]) for c in recent]
            lows = [float(c["low"]) for c in recent]
            start_price = closes[0]
            end_price = closes[-1]
            pct_change = ((end_price - start_price) / start_price) * 100 if start_price else 0
            return {
                "trend": "bearish" if pct_change < -1 else "bullish" if pct_change > 1 else "consolidating",
                "change_pct": round(pct_change, 2),
                "range_high": max(highs),
                "range_low": min(lows),
                "current_price": end_price,
                "candles_analyzed": len(recent),
            }

        elif tool_name == "get_volume_profile":
            ohlcv = await self.mcp.get_ohlcv(ticker, "CRYPTO", timeframe="1h", limit=24)
            if not ohlcv:
                return {"error": f"No data for {ticker}"}
            volumes = [float(c.get("volume", 0)) for c in ohlcv]
            avg_vol = sum(volumes) / len(volumes) if volumes else 0
            recent_vol = volumes[-1] if volumes else 0
            return {
                "recent_volume": recent_vol,
                "avg_24h_volume": avg_vol,
                "volume_ratio": round(recent_vol / avg_vol, 2) if avg_vol > 0 else 0,
                "trend": "increasing" if recent_vol > avg_vol * 1.2 else "decreasing" if recent_vol < avg_vol * 0.8 else "stable",
            }

        elif tool_name == "get_market_regime":
            try:
                from src.advisory.crypto_regime import CryptoRegimeFilter
                engine = CryptoRegimeFilter(self.mcp)
                regime = await engine.get_current_regime()
                return regime
            except Exception as e:
                return {"error": f"Regime unavailable: {e}"}

        return {"error": f"Unknown tool: {tool_name}"}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_task(self, data: dict, escalated: bool) -> str:
        """Build user prompt with position context."""
        lines = [
            f"POSITION: {data.get('ticker', '?')} {data.get('side', '?')}",
            f"Entry: {data.get('entry_price', '?')} | Current: {data.get('current_price', '?')}",
            f"Gain: {data.get('gain_pct', 0):+.2f}% | Hours held: {data.get('hours_held', 0):.1f}",
            f"Bucket: {data.get('bucket', 'standard')}",
            f"Gate reason: {data.get('gate_reason', '?')}",
        ]

        # Phase 3A: Add momentum/trend data for cheap pass
        if not escalated:
            consecutive_holds = data.get("consecutive_holds", 0)
            if consecutive_holds > 0:
                lines.append(f"Consecutive AI holds: {consecutive_holds} (if >=3 and negative, forced EXIT)")
            # Gain change if available (from prior judgment tracking)
            gain_change = data.get("gain_change_since_last_check")
            if gain_change is not None:
                if gain_change < -1.0:
                    trend = "BLEEDING"
                elif abs(gain_change) < 1.0:
                    trend = "FLAT"
                else:
                    trend = "PUMPING"
                lines.append(f"Momentum: {trend} ({gain_change:+.2f}% since last check)")

        if escalated and data.get("prior_judgment"):
            pj = data["prior_judgment"]
            lines.append(f"\nPRIOR JUDGE (cheap pass): {pj.get('action', '?')} "
                         f"(confidence {pj.get('confidence', 0)}) — {pj.get('reason', '?')}")
            lines.append("Position still underperforming after prior HOLD. Use tools to verify.")

        return "\n".join(lines)

    def _normalize_result(self, result: dict, position_data: dict) -> dict:
        """Normalize agent output to standard judgment schema."""
        # Phase 1C: Strip markdown JSON blocks if LLM wrapped response
        if isinstance(result, str):
            match = re.search(r'\{.*\}', result, re.DOTALL)
            if match:
                try:
                    result = json.loads(match.group(0))
                except json.JSONDecodeError:
                    result = {"error": "Failed to parse LLM JSON string"}
            else:
                result = {"error": "No JSON found in LLM response"}

        if "error" in result:
            logger.warning("judge_error", ticker=position_data.get("ticker"), error=result["error"])
            return {
                "action": "EXIT",
                "confidence": 80,
                "reason": f"Judge error — fail-safe exit: {result.get('error', 'unknown')}",
                "new_stop_pct": None,
            }

        action = result.get("action", "EXIT").upper()
        if action not in ("HOLD", "EXIT", "TIGHTEN_STOP"):
            action = "EXIT"

        confidence = result.get("confidence", 50)
        if not isinstance(confidence, (int, float)):
            confidence = 50
        confidence = max(0, min(100, int(confidence)))

        return {
            "action": action,
            "confidence": confidence,
            "reason": result.get("reason", "no reason"),
            "new_stop_pct": result.get("new_stop_pct"),
        }
