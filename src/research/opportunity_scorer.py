"""Opportunity Scoring Engine — Weighted composite scoring.

Aggregates all intelligence module scores into a single 0-100 opportunity score.
Weights: Fundamental 25%, Narrative 15%, Smart Money 15%, On-chain 15%,
         Developer 10%, Community 8%, Market 7%, Technical 5%

Pure math — no LLM calls.
"""

import asyncio
from src.utils.logging import get_logger

logger = get_logger("opportunity_scorer")

# Sniper / Conservative weights optimized for real edge
DEFAULT_WEIGHTS = {
    "technical":    0.30,  # Is there a real breakout happening NOW?
    "smart_money":  0.25,  # Are institutions quietly accumulating?
    "fundamental":  0.20,  # Is the project real and solid?
    "onchain":      0.10,  # TVL/DEX activity proving real usage?
    "narrative":    0.10,  # Is this sector hot right now?
    "developer":    0.03,  # Are devs still building?
    "community":    0.01,  # Community momentum
    "market":       0.01,  # Overall market regime
}


class OpportunityScorer:
    """Multi-dimensional opportunity scoring engine."""

    def __init__(self, cache=None, weights: dict | None = None, bybit_client=None):
        self._cache = cache
        self.weights = weights or dict(DEFAULT_WEIGHTS)
        self._bybit = bybit_client

    async def score_opportunity(self, symbol: str, coingecko_id: str | None = None,
                                 contract: str | None = None, chain: str = "ethereum") -> dict:
        """Score a token across all intelligence dimensions.

        Returns dict with individual scores, composite, confidence, bucket, and evidence.
        """
        from src.research.onchain_intel import OnchainIntelligence
        from src.research.developer_intel import DeveloperIntelligence
        from src.research.community_intel import CommunityIntelligence
        from src.research.narrative_intel import NarrativeIntelligence
        from src.research.smart_money_intel import SmartMoneyIntelligence
        from src.research.risk_intel import RiskIntelligence
        from src.advisory.crypto_regime import get_crypto_regime
        from src.advisory.crypto_technicals import full_analysis

        onchain = OnchainIntelligence(cache=self._cache)
        developer = DeveloperIntelligence(cache=self._cache)
        community = CommunityIntelligence(cache=self._cache)
        narrative = NarrativeIntelligence(cache=self._cache)
        smart_money = SmartMoneyIntelligence(cache=self._cache)
        risk = RiskIntelligence(cache=self._cache)

        try:
            # Run all intel modules in parallel
            results = await asyncio.gather(
                onchain.snapshot(symbol, chain),
                developer.snapshot(symbol, coingecko_id),
                community.snapshot(symbol, coingecko_id),
                narrative.analyze_token(symbol, coingecko_id),
                smart_money.detect_accumulation(symbol, contract, chain),
                risk.full_assessment(symbol, contract, chain, coingecko_id),
                return_exceptions=True,
            )

            # Extract scores (default 0 on failure)
            onchain_score = results[0].get("score", 0) if isinstance(results[0], dict) else 0
            developer_score = results[1].get("score", 0) if isinstance(results[1], dict) else 0
            community_score = results[2].get("score", 0) if isinstance(results[2], dict) else 0
            narrative_score = results[3].get("score", 0) if isinstance(results[3], dict) else 0
            smart_money_score = results[4].get("score", 0) if isinstance(results[4], dict) else 0
            risk_data = results[5] if isinstance(results[5], dict) else {}
            risk_score = risk_data.get("risk_score", 50)

            # Market score (from regime data — reuse existing)
            market_score = 50  # neutral default
            try:
                regime = get_crypto_regime()
                if regime:
                    regime_state = regime.get("regime", "CHOP")
                    if regime_state == "TREND_BULL":
                        market_score = 80
                    elif regime_state == "TREND_BEAR":
                        market_score = 20
                    elif regime_state == "MEAN_REVERSION":
                        market_score = 50
                    else:
                        market_score = 40
            except Exception:
                pass

            # Technical score via actual OHLCV indicators
            technical_score = 50
            if self._bybit:
                try:
                    ohlcv = await self._bybit.get_ohlcv(symbol, interval="1h", limit=100)
                    if ohlcv and len(ohlcv) >= 50:
                        tech_data = full_analysis(ohlcv)
                        score = 50
                        
                        rsi = tech_data.get("rsi_14", 50)
                        if 40 <= rsi <= 70:
                            score += 10
                        elif rsi > 70:
                            score -= 10
                            
                        trend = tech_data.get("trend_ema", "neutral")
                        if trend == "bullish":
                            score += 20
                        elif trend == "bearish":
                            score -= 20
                            
                        macd = tech_data.get("macd_hist", 0)
                        if macd > 0:
                            score += 10
                        
                        bb = tech_data.get("bb_position", 0.5)
                        if bb < 0.2:
                            score += 10  # near lower band support
                        elif bb > 0.8:
                            score -= 10  # near upper band resistance
                            
                        technical_score = max(0, min(100, score))
                except Exception as e:
                    logger.warning("technical_score_failed", symbol=symbol, error=str(e))

            # Fundamental score via LLM intelligence
            fundamental_score = 50
            try:
                from src.research.fundamental_intel import FundamentalIntelligence
                fundamental = FundamentalIntelligence(cache=self._cache)
                ctx = {}
                if isinstance(results[0], dict):
                    ctx["onchain"] = results[0]
                if isinstance(results[1], dict):
                    ctx["dev_activity"] = results[1]
                
                fund_result = await fundamental.analyze(symbol, context=ctx)
                if isinstance(fund_result, dict):
                    fundamental_score = fund_result.get("composite_score", 50)
            except Exception as e:
                logger.warning("fundamental_score_failed", symbol=symbol, error=str(e))

            # Weighted composite
            scores = {
                "fundamental": fundamental_score,
                "narrative": narrative_score,
                "smart_money": smart_money_score,
                "onchain": onchain_score,
                "developer": developer_score,
                "community": community_score,
                "market": market_score,
                "technical": technical_score,
            }

            composite = sum(scores[k] * self.weights.get(k, 0) for k in scores)

            # Risk deduction
            risk_deduction = 0
            if risk_score > 75:
                risk_deduction = 20
            elif risk_score > 50:
                risk_deduction = 10
            composite = max(0, composite - risk_deduction)

            # Confidence: based on data completeness
            modules_with_data = sum(1 for r in results if isinstance(r, dict) and r.get("score", 0) > 0)
            confidence = round((modules_with_data / 6) * 100, 2)

            # Bucket classification
            bucket = self.classify_bucket(composite)

            return {
                "symbol": symbol,
                "composite_score": round(composite, 2),
                "confidence": confidence,
                "risk_category": risk_data.get("risk_category", "UNKNOWN"),
                "risk_score": risk_score,
                "investment_bucket": bucket,
                "scores": scores,
                "risk_deduction": risk_deduction,
                "evidence": {
                    "onchain": results[0] if isinstance(results[0], dict) else {},
                    "developer": results[1] if isinstance(results[1], dict) else {},
                    "community": results[2] if isinstance(results[2], dict) else {},
                    "narrative": results[3] if isinstance(results[3], dict) else {},
                    "smart_money": results[4] if isinstance(results[4], dict) else {},
                    "risk": risk_data,
                },
            }
        finally:
            # Close all HTTP clients to prevent aiohttp connection leaks (Finding 5)
            for intel in (onchain, developer, community, narrative, smart_money, risk):
                await intel.close()

    @staticmethod
    def classify_bucket(score: float) -> str:
        """Classify into investment bucket based on composite score."""
        if score >= 80:
            return "CORE"
        elif score >= 60:
            return "GROWTH"
        elif score >= 40:
            return "SPECULATIVE"
        else:
            return "MOONSHOT"

    async def persist_report(self, result: dict):
        """Save research report to database."""
        from src.models.database import async_session
        from sqlalchemy import text
        import json

        async with async_session() as session:
            await session.execute(
                text("""INSERT INTO research_reports
                    (symbol, report_type, fundamental_score, narrative_score,
                     smart_money_score, onchain_score, developer_score,
                     community_score, market_score, technical_score,
                     opportunity_score, confidence, risk_category,
                     investment_bucket, evidence, risks, recommendation)
                    VALUES (:symbol, 'quick', :fundamental, :narrative,
                            :smart_money, :onchain, :developer,
                            :community, :market, :technical,
                            :opportunity, :confidence, :risk_cat,
                            :bucket, :evidence, :risks, :recommendation)"""),
                {
                    "symbol": result["symbol"],
                    "fundamental": result["scores"]["fundamental"],
                    "narrative": result["scores"]["narrative"],
                    "smart_money": result["scores"]["smart_money"],
                    "onchain": result["scores"]["onchain"],
                    "developer": result["scores"]["developer"],
                    "community": result["scores"]["community"],
                    "market": result["scores"]["market"],
                    "technical": result["scores"]["technical"],
                    "opportunity": result["composite_score"],
                    "confidence": result["confidence"],
                    "risk_cat": result["risk_category"],
                    "bucket": result["investment_bucket"],
                    "evidence": json.dumps(result["evidence"]),
                    "risks": json.dumps({"risk_score": result["risk_score"]}),
                    "recommendation": "BUY" if result["composite_score"] >= 70 else ("WATCH" if result["composite_score"] >= 40 else "AVOID"),
                },
            )
            await session.commit()
