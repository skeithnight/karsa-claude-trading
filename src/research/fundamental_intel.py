"""Fundamental Intelligence — LLM-powered team/product/tech/business analysis.

Uses BaseAgent tool-use loop for research synthesis.
LLM calls only for this module — all others are deterministic.
"""

from src.utils.logging import get_logger

logger = get_logger("fundamental_intel")

_SYSTEM_PROMPT = """You are a crypto fundamental analyst. Analyze the given project across 4 dimensions:
1. TEAM (0-100): Known team? Previous startups? GitHub history? Technical experience? Security track record?
2. PRODUCT (0-100): MVP exists? Working app? User adoption? Revenue? Product-market fit?
3. TECHNOLOGY (0-100): Innovation? Infrastructure quality? Scalability? Security audits? AI integration?
4. BUSINESS (0-100): TAM size? Competition? Revenue model? Ecosystem partnerships? Sustainability?

Respond with ONLY a valid JSON object:
{
  "team_score": 0-100,
  "product_score": 0-100,
  "technology_score": 0-100,
  "business_score": 0-100,
  "team_reasoning": "brief explanation",
  "product_reasoning": "brief explanation",
  "technology_reasoning": "brief explanation",
  "business_reasoning": "brief explanation",
  "composite_score": 0-100,
  "key_strengths": ["strength1", "strength2"],
  "key_risks": ["risk1", "risk2"]
}

composite_score = team*0.25 + product*0.30 + technology*0.25 + business*0.20
Be conservative. Score 50 if data is insufficient. Only score >80 with strong evidence."""


class FundamentalIntelligence:
    """LLM-powered fundamental analysis."""

    def __init__(self, cache=None):
        self._cache = cache

    async def analyze(self, symbol: str, context: dict | None = None) -> dict:
        """Run LLM fundamental analysis on a token.

        Args:
            symbol: Token symbol (e.g., "ETHUSDT")
            context: Pre-collected data from other intel modules
        """
        from src.config import settings
        import anthropic

        # Build context string from available data
        ctx_parts = [f"TOKEN: {symbol}"]
        if context:
            if context.get("coingecko_detail"):
                d = context["coingecko_detail"]
                ctx_parts.append(f"Name: {d.get('name')}")
                ctx_parts.append(f"Categories: {', '.join(d.get('categories', []))}")
                ctx_parts.append(f"Description: {(d.get('description') or '')[:500]}")
                links = d.get("links", {})
                if links.get("homepage"):
                    ctx_parts.append(f"Website: {links['homepage']}")
                if links.get("github"):
                    ctx_parts.append(f"GitHub: {', '.join(links['github'][:3])}")
                md = d.get("market_data", {})
                ctx_parts.append(f"Market Cap: ${md.get('market_cap', 0):,.0f}")
                ctx_parts.append(f"FDV: ${md.get('fdv', 0):,.0f}")
                ctx_parts.append(f"24h Volume: ${md.get('volume_24h', 0):,.0f}")
            if context.get("dev_activity"):
                dev = context["dev_activity"]
                ctx_parts.append(f"GitHub Stars: {dev.get('stars', 'N/A')}")
                ctx_parts.append(f"Commits (30d): {dev.get('commits_30d', 'N/A')}")
                ctx_parts.append(f"Contributors: {dev.get('contributors_active', 'N/A')}")
            if context.get("onchain"):
                oc = context["onchain"]
                ctx_parts.append(f"TVL: ${oc.get('tvl_usd', 0):,.0f}")
                ctx_parts.append(f"DEX Volume 24h: ${oc.get('dex_volume_24h_usd', 0):,.0f}")

        user_prompt = "\n".join(ctx_parts)

        try:
            from src.agents.base import LLM_BASE_URL, LLM_AUTH_TOKEN, LLM_MODEL
            client = anthropic.Anthropic(base_url=LLM_BASE_URL, api_key=LLM_AUTH_TOKEN)

            import asyncio
            response = await asyncio.to_thread(
                client.messages.create,
                model=LLM_MODEL,
                max_tokens=1024,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            import json
            text = response.content[0].text
            # Extract JSON from response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(text[start:end])
                return {
                    "symbol": symbol,
                    "team_score": result.get("team_score", 50),
                    "product_score": result.get("product_score", 50),
                    "technology_score": result.get("technology_score", 50),
                    "business_score": result.get("business_score", 50),
                    "composite_score": result.get("composite_score", 50),
                    "key_strengths": result.get("key_strengths", []),
                    "key_risks": result.get("key_risks", []),
                    "reasoning": {
                        "team": result.get("team_reasoning", ""),
                        "product": result.get("product_reasoning", ""),
                        "technology": result.get("technology_reasoning", ""),
                        "business": result.get("business_reasoning", ""),
                    },
                }
        except Exception as e:
            logger.error("fundamental_analysis_failed", symbol=symbol, error=str(e))

        # Fallback: neutral scores
        return {
            "symbol": symbol,
            "team_score": 50,
            "product_score": 50,
            "technology_score": 50,
            "business_score": 50,
            "composite_score": 50,
            "key_strengths": [],
            "key_risks": ["insufficient_data"],
            "reasoning": {},
            "error": "analysis_failed",
        }

    def compute_score(self, result: dict) -> float:
        """Extract composite score from analysis result."""
        return result.get("composite_score", 50)
