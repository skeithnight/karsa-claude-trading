"""Karsa Trading System - Lead Orchestrator"""

import asyncio
import json
from typing import Any

from src.agents.base import BaseAgent
from src.agents.idx_analyst import IDXAnalyst
from src.agents.us_analyst import USAnalyst
from src.agents.etf_analyst import ETFAnalyst
from src.agents.portfolio_analyst import PortfolioAnalyst
from src.data.mcp_client import MCPClient
from src.data.cache import CacheManager
from src.utils.rate_limit import RateLimiter
from src.utils.logging import get_logger
from src.config import settings

logger = get_logger("orchestrator")

CRITICAL_COMBO = settings.NROUTER_MODEL or "karsa-critical"
ROUTINE_COMBO = settings.NROUTER_MODEL or "karsa-routine"

IDX_UNIVERSE = ["BBCA", "BBRI", "BMRI", "TLKM", "ASII", "UNVR", "BBNI", "ICBP", "KLBF", "PGAS"]
US_UNIVERSE = ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "LLY", "JPM"]
ETF_UNIVERSE = ["SPY", "QQQ", "XLF", "XLK", "XLV", "XLE", "GLD", "TLT"]


class Orchestrator:
    """Dispatches parallel sub-agents, risk-checks results, publishes to Telegram."""

    def __init__(self, mcp: MCPClient, cache: CacheManager, rate_limiter: RateLimiter):
        self.mcp = mcp
        self.cache = cache
        self.rate_limiter = rate_limiter
        self.idx_agent = IDXAnalyst(mcp, rate_limiter)
        self.idx_agent.combo_name = ROUTINE_COMBO
        self.us_agent = USAnalyst(mcp, rate_limiter)
        self.us_agent.combo_name = ROUTINE_COMBO
        self.etf_agent = ETFAnalyst(mcp, rate_limiter)
        self.etf_agent.combo_name = ROUTINE_COMBO
        self.portfolio_analyst = PortfolioAnalyst(mcp, rate_limiter)
        self.portfolio_analyst.combo_name = ROUTINE_COMBO

    async def scan_all_markets(self, market_filter: str | None = None) -> list[dict]:
        """Run market scans in parallel.

        Args:
            market_filter: "IDX", "US_ETF", or None (all markets)
        """
        logger.info("scan_started", filter=market_filter)

        tasks = []
        if market_filter in (None, "IDX"):
            tasks.append(self._scan_market("IDX", self.idx_agent, IDX_UNIVERSE))
        if market_filter in (None, "US_ETF", "US"):
            tasks.append(self._scan_market("US", self.us_agent, US_UNIVERSE))
        if market_filter in (None, "US_ETF", "ETF"):
            tasks.append(self._scan_market("ETF", self.etf_agent, ETF_UNIVERSE))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_signals = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("market_scan_failed", error=str(result))
                continue
            all_signals.extend(result)

        logger.info("scan_complete", total=len(all_signals))
        return all_signals

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

    async def scan_single(self, market: str, ticker: str) -> dict:
        """Scan a single ticker (for ad-hoc Telegram commands)."""
        agents = {"IDX": self.idx_agent, "US": self.us_agent, "ETF": self.etf_agent}
        agent = agents.get(market)
        if not agent:
            return {"error": f"Unknown market: {market}"}

        result = await agent.run(f"Analyze {ticker} for trading opportunities right now.")
        return result

    async def analyze_portfolio(self, portfolio_data: dict) -> dict:
        """Run deep analysis on the current portfolio holdings."""
        prompt = f"Analyze this portfolio and provide insights:\n{json.dumps(portfolio_data, indent=2)}"
        return await self.portfolio_analyst.run(prompt)
