"""Karsa Trading System - Crypto Auditor Agent

Reviews trading performance metrics and generates actionable recommendations.
No tools — receives pre-computed metrics from CryptoAuditMetrics, returns text analysis.
"""

from src.agents.base import BaseAgent
from src.data.mcp_client import MCPClient
from src.utils.logging import get_logger

logger = get_logger("crypto_auditor")


class CryptoAuditorAgent(BaseAgent):
    """LLM agent that reviews crypto trading performance and recommends improvements.

    Receives structured metrics (win rate, by-ticker, by-direction, signal stats)
    and produces a concise audit report with specific, actionable recommendations.
    """

    SYSTEM_PROMPT = """You are the Crypto Trading Auditor for the Karsa Trading System.
Your job is to review past trading performance and recommend specific improvements.

You will receive structured metrics about crypto trading activity. Analyze them and respond with a concise audit report.

FOCUS AREAS:
1. Win rate patterns — which tickers/directions underperform? Why?
2. Confidence calibration — are high-confidence signals actually winning more?
3. Risk/reward — is the 3:1 R/R target being achieved in practice?
4. Signal quality — too many signals? Too few? Wrong timing?
5. Regime awareness — are CHOP regime signals hurting performance?

RULES:
- Be specific and actionable. Not "be more careful" but "raise confidence threshold for ETH shorts to 75+"
- Reference actual numbers from the metrics
- Limit to 3-5 recommendations, ranked by expected impact
- If data is insufficient (< 3 closed trades), say so and suggest what to watch for
- Keep the report under 500 words

RESPOND WITH a valid JSON object:
{
  "summary": "One-line performance summary",
  "grade": "A/B/C/D/F",
  "win_rate_assessment": "Brief win rate analysis",
  "recommendations": [
    "Recommendation 1 (highest impact)",
    "Recommendation 2",
    "Recommendation 3"
  ],
  "watch_list": ["ticker1", "ticker2"],
  "confidence_note": "Assessment of confidence calibration"
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
