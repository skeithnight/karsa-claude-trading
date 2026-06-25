"""Karsa Trading System - Lead Orchestrator"""

import asyncio
import json
from typing import Any

from src.agents.base import BaseAgent
from src.agents.idx_analyst import IDXAnalyst
from src.agents.us_analyst import USAnalyst
from src.agents.etf_analyst import ETFAnalyst
from src.agents.risk_manager import RiskManager
from src.data.mcp_client import MCPClient
from src.data.idx_adapter import IDXDataAdapter
from src.data.cache import CacheManager
from src.utils.rate_limit import RateLimiter
from src.utils.logging import get_logger

logger = get_logger("orchestrator")

IDX_UNIVERSE = ["BBCA", "BBRI", "BMRI", "TLKM", "ASII", "UNVR", "BBNI", "ICBP", "KLBF", "PGAS"]
US_UNIVERSE = ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "LLY", "JPM"]
ETF_UNIVERSE = ["SPY", "QQQ", "XLF", "XLK", "XLV", "XLE", "GLD", "TLT"]


class Orchestrator:
    """Dispatches parallel sub-agents, risk-checks results, publishes to Telegram."""

    def __init__(self, mcp: MCPClient, idx_adapter: IDXDataAdapter,
                 cache: CacheManager, rate_limiter: RateLimiter):
        self.mcp = mcp
        self.cache = cache
        self.rate_limiter = rate_limiter
        self.idx_agent = IDXAnalyst(mcp, idx_adapter, rate_limiter)
        self.us_agent = USAnalyst(mcp, rate_limiter)
        self.etf_agent = ETFAnalyst(mcp, rate_limiter)
        self.risk_manager = RiskManager(mcp, rate_limiter)

    async def scan_all_markets(self) -> list[dict]:
        """Run all market scans in parallel and return validated signals."""
        logger.info("scan_started")

        results = await asyncio.gather(
            self._scan_market("IDX", self.idx_agent, IDX_UNIVERSE),
            self._scan_market("US", self.us_agent, US_UNIVERSE),
            self._scan_market("ETF", self.etf_agent, ETF_UNIVERSE),
            return_exceptions=True,
        )

        all_signals = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("market_scan_failed", error=str(result))
                continue
            all_signals.extend(result)

        strong = [s for s in all_signals if s.get("confidence_score", 0) >= 60]
        logger.info("scan_complete", total=len(all_signals), strong=len(strong))

        validated = []
        for signal in strong:
            risk_result = await self._check_risk(signal)
            if risk_result.get("approved"):
                signal["risk_check"] = risk_result
                validated.append(signal)
                await self.cache.publish_signal(signal)
                logger.info("signal_approved", ticker=signal["ticker"], confidence=signal["confidence_score"])
            else:
                logger.info("signal_rejected", ticker=signal["ticker"],
                            reason=risk_result.get("rejection_reason"))

        return validated

    async def _scan_market(self, market: str, agent: BaseAgent, universe: list[str]) -> list[dict]:
        signals = []
        for ticker in universe:
            try:
                result = await agent.run(f"Analyze {ticker} for trading opportunities right now.")
                if result.get("error"):
                    logger.warning("agent_error", market=market, ticker=ticker, error=result["error"])
                    continue
                if result.get("confidence_score", 0) >= 50:
                    signals.append(result)
            except Exception as e:
                logger.error("ticker_scan_failed", market=market, ticker=ticker, error=str(e))
        logger.info("market_scan_done", market=market, tickers=len(universe), signals=len(signals))
        return signals

    async def _check_risk(self, signal: dict) -> dict:
        try:
            return await self.risk_manager.run(f"Validate this trade signal:\n{json.dumps(signal, indent=2)}")
        except Exception as e:
            logger.error("risk_check_failed", ticker=signal.get("ticker"), error=str(e))
            return {"approved": False, "rejection_reason": f"Risk check error: {e}"}

    async def scan_single(self, market: str, ticker: str) -> dict:
        """Scan a single ticker (for ad-hoc Telegram commands)."""
        agents = {"IDX": self.idx_agent, "US": self.us_agent, "ETF": self.etf_agent}
        agent = agents.get(market)
        if not agent:
            return {"error": f"Unknown market: {market}"}

        result = await agent.run(f"Analyze {ticker} for trading opportunities right now.")
        if result.get("confidence_score", 0) >= 60:
            result["risk_check"] = await self._check_risk(result)
        return result
