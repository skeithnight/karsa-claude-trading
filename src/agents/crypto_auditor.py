"""Karsa Trading System - Crypto Auditor Agent

Reviews trading performance metrics and generates actionable recommendations.
No tools — receives pre-computed metrics from CryptoAuditMetrics, returns text analysis.

Deterministic pre-filter: rejects obviously bad signals before LLM call to save cost.

Phase 4: Self-improvement — outputs recommendations to strategy_recommendations table.
"""

from src.agents.base import BaseAgent
from src.advisory.crypto_technicals import calculate_rsi
from src.data.mcp_client import MCPClient
from src.utils.logging import get_logger

logger = get_logger("crypto_auditor")


# --- Deterministic Pre-Filter ---

_PREFILTER_RULES = [
    {
        "name": "extreme_rsi_long",
        "desc": "RSI > 85 — overbought, reject LONG",
        "check": lambda signal, rsi: signal.get("direction") == "LONG" and rsi > 85,
    },
    {
        "name": "extreme_rsi_short",
        "desc": "RSI < 15 — oversold, reject SHORT",
        "check": lambda signal, rsi: signal.get("direction") == "SHORT" and rsi < 15,
    },
]


async def prefilter_signal(signal: dict, mcp: MCPClient) -> dict:
    """Run deterministic checks before LLM auditor. Reject obviously bad signals.

    Returns: {"pass": bool, "reason": str, "checks": list}
    """
    ticker = signal.get("ticker", "")
    direction = signal.get("direction", "")
    checks = []

    # Check RSI
    try:
        ohlcv = await mcp.get_ohlcv(ticker, "CRYPTO", timeframe="4h", limit=30)
        if ohlcv and len(ohlcv) >= 15:
            rsi_data = calculate_rsi(ohlcv, 14)
            rsi = rsi_data.get("rsi", 50)
            checks.append({"name": "rsi", "value": rsi, "signal": rsi_data.get("signal")})

            for rule in _PREFILTER_RULES:
                if rule["check"](signal, rsi):
                    return {
                        "pass": False,
                        "reason": f"Pre-filter rejected: {rule['desc']} (RSI={rsi})",
                        "checks": checks,
                    }
    except Exception as e:
        logger.warning("prefilter_rsi_failed", error=str(e))
        checks.append({"name": "rsi", "error": str(e)})

    # Check funding rate (reject LONG if funding > 0.1% — crowded long)
    try:
        funding = await mcp.get_funding_rate(ticker)
        rate = funding.get("funding_rate", 0)
        checks.append({"name": "funding_rate", "value": rate})

        if direction == "LONG" and rate > 0.001:
            return {
                "pass": False,
                "reason": f"Pre-filter rejected: funding rate {rate*100:.3f}% too high for LONG (crowded long)",
                "checks": checks,
            }
        elif direction == "SHORT" and rate < -0.001:
            return {
                "pass": False,
                "reason": f"Pre-filter rejected: funding rate {rate*100:.3f}% too negative for SHORT (crowded short)",
                "checks": checks,
            }
    except Exception as e:
        logger.warning("prefilter_funding_failed", error=str(e))
        checks.append({"name": "funding_rate", "error": str(e)})

    return {"pass": True, "reason": "All pre-filter checks passed", "checks": checks}


class CryptoAuditorAgent(BaseAgent):
    """LLM agent that reviews crypto trading performance and recommends improvements.

    Receives structured metrics (win rate, by-ticker, by-direction, signal stats)
    and produces a concise audit report with specific, actionable recommendations.
    """

    SYSTEM_PROMPT = """You are the Crypto Trading Auditor for the Karsa Trading System.
Your job is to review past trading performance and recommend specific improvements.

You will receive structured metrics about crypto trading activity including:
- Basic performance (win rate, PnL, by ticker, by direction)
- Confidence calibration (do high-confidence signals actually win more?)
- Time-of-day patterns (which hours produce best/worst results?)
- Strategy performance (which strategies are working?)
- Regime performance (which market regimes produce best results?)

FOCUS AREAS:
1. Win rate patterns — which tickers/directions underperform? Why?
2. Confidence calibration — are high-confidence signals actually winning more?
3. Risk/reward — is the 3:1 R/R target being achieved in practice?
4. Signal quality — too many signals? Too few? Wrong timing?
5. Regime awareness — are CHOP regime signals hurting performance?
6. Time-of-day — should we avoid trading certain hours?
7. Strategy effectiveness — should we adjust strategy weights?

RULES:
- Be specific and actionable. Not "be more careful" but "raise confidence threshold for ETH shorts to 75+"
- Reference actual numbers from the metrics
- Limit to 3-5 recommendations, ranked by expected impact
- Each recommendation must have a type, priority, and expected impact
- If data is insufficient (< 3 closed trades), say so and suggest what to watch for
- Keep the report under 500 words

RESPOND WITH a valid JSON object:
{
  "summary": "One-line performance summary",
  "grade": "A/B/C/D/F",
  "win_rate_assessment": "Brief win rate analysis",
  "recommendations": [
    {
      "type": "STRATEGY|RISK|TIMING|UNIVERSE",
      "priority": "HIGH|MEDIUM|LOW",
      "title": "Short actionable title",
      "description": "Detailed recommendation with specific numbers",
      "expected_impact": "Expected improvement if applied"
    }
  ],
  "watch_list": ["ticker1", "ticker2"],
  "confidence_note": "Assessment of confidence calibration",
  "regime_note": "Assessment of regime-based performance",
  "time_note": "Assessment of time-of-day patterns"
}"""

    TOOLS = []  # No tools — metrics are passed as context

    def __init__(self, mcp: MCPClient):
        super().__init__(
            name="crypto_auditor",
            combo_name="karsa-routine",
            system_prompt=self.SYSTEM_PROMPT,
            tools=self.TOOLS,
            mcp=mcp,
        )

    async def run_audit(self, metrics: dict) -> dict:
        """Run audit on pre-computed metrics.

        Args:
            metrics: Output from CryptoAuditMetrics.gather()

        Returns:
            Auditor's analysis as dict with summary, grade, recommendations
        """
        import json

        prompt = (
            "Analyze the following crypto trading performance metrics "
            f"from the last {metrics.get('period_days', 7)} days:\n\n"
            + json.dumps(metrics, indent=2, default=str)
        )

        result = await self.run(prompt)

        if result.get("error"):
            logger.error("audit_agent_error", error=result["error"])
            return {
                "summary": "Audit failed — agent error",
                "grade": "?",
                "recommendations": [f"Error: {result['error']}"],
            }

        return result

    async def save_recommendations(self, analysis: dict, metrics_snapshot: dict | None = None):
        """Persist audit recommendations to strategy_recommendations table.

        Args:
            analysis: Output from run_audit()
            metrics_snapshot: Raw metrics for reference (optional)
        """
        try:
            from src.models.database import async_session
            from src.models.tables import StrategyRecommendation

            recommendations = analysis.get("recommendations", [])
            if not recommendations:
                return

            async with async_session() as session:
                for rec in recommendations:
                    # Handle both string and structured recommendations
                    if isinstance(rec, str):
                        session.add(StrategyRecommendation(
                            recommendation_type="GENERAL",
                            priority="MEDIUM",
                            title=rec[:200],
                            description=rec,
                            expected_impact=None,
                            metrics_snapshot=metrics_snapshot,
                        ))
                    elif isinstance(rec, dict):
                        session.add(StrategyRecommendation(
                            recommendation_type=rec.get("type", "GENERAL"),
                            priority=rec.get("priority", "MEDIUM"),
                            title=rec.get("title", "")[:200],
                            description=rec.get("description", ""),
                            expected_impact=rec.get("expected_impact"),
                            metrics_snapshot=metrics_snapshot,
                        ))

                await session.commit()
                logger.info("recommendations_saved", count=len(recommendations))
        except Exception as e:
            logger.error("save_recommendations_failed", error=str(e))
