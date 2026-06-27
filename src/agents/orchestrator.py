"""Karsa Trading System - Lead Orchestrator"""

import asyncio
import json

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

ROUTINE_COMBO = settings.NROUTER_MODEL or "karsa-routine"

IDX_UNIVERSE = ["BBCA", "BBRI", "BMRI", "TLKM", "ASII", "UNVR", "BBNI", "ICBP", "KLBF", "PGAS"]
US_UNIVERSE = ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "LLY", "JPM"]
ETF_UNIVERSE = ["SPY", "QQQ", "XLF", "XLK", "XLV", "XLE", "GLD", "TLT"]

# Required fields for a valid signal from an agent
_REQUIRED_SIGNAL_FIELDS = {"ticker", "confidence_score", "direction"}
_VALID_DIRECTIONS = {"LONG", "SHORT", "CLOSE"}


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
        # Emergency stop gate — block all scans if active
        from src.risk import emergency
        if await emergency.is_active():
            logger.warning("scan_blocked_emergency_stop")
            return []

        logger.info("scan_started", filter=market_filter)

        tasks = []
        if market_filter in (None, "IDX"):
            tasks.append(self._scan_market("IDX", self.idx_agent, IDX_UNIVERSE))
        if market_filter in (None, "US_ETF", "US"):
            tasks.append(self._scan_market("US", self.us_agent, US_UNIVERSE))
        if market_filter in (None, "US_ETF", "ETF"):
            # Regime hard veto: disable ETF mean reversion in BEAR regime
            skip_etf = await self._is_bear_regime()
            if skip_etf:
                logger.warning("etf_scan_skipped_bear_regime", reason="VIX>25 or SPY<200-SMA")
            else:
                tasks.append(self._scan_market("ETF", self.etf_agent, ETF_UNIVERSE))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_signals = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("market_scan_failed", error=str(result))
                continue
            all_signals.extend(result)

        # Persist all signals to database
        for signal in all_signals:
            await self._save_signal(signal)

        logger.info("scan_complete", total=len(all_signals))
        return all_signals

    async def _scan_market(self, market: str, agent: BaseAgent, universe: list[str]) -> list[dict]:
        signals = []
        for ticker in universe:
            # Per-ticker emergency check — allows partial completion if stop activates mid-scan
            from src.risk import emergency
            if await emergency.is_active():
                logger.warning("scan_aborted_emergency_stop", market=market, ticker=ticker)
                break

            try:
                from src.utils.validation import sanitize_for_prompt
                safe_ticker = sanitize_for_prompt(ticker)
                result = await agent.run(f"Analyze {safe_ticker} for trading opportunities right now.")
                if result.get("error"):
                    logger.warning("agent_error", market=market, ticker=ticker, error=result["error"])
                    continue

                # Validate signal structure
                issues = self._validate_signal(result, market)
                if issues:
                    logger.warning("invalid_signal", market=market, ticker=ticker, issues=issues)
                    continue

                if result.get("confidence_score", 0) >= 50:
                    signals.append(result)
            except Exception as e:
                logger.error("ticker_scan_failed", market=market, ticker=ticker, error=str(e))
        logger.info("market_scan_done", market=market, tickers=len(universe), signals=len(signals))
        return signals

    async def _is_bear_regime(self) -> bool:
        """Check if US market is in BEAR regime (hard veto for ETF mean reversion).

        Returns True if VIX > 25 or SPY below 200-SMA.
        Uses the same data source as the regime filter.
        """
        try:
            from src.advisory.regime import USRegimeFilter
            us_filter = USRegimeFilter(self.mcp)
            regime = await us_filter.get_current_regime()
            state = regime.get("state", "NEUTRAL")
            return state == "BEAR"
        except Exception as e:
            logger.error("regime_check_failed", error=str(e))
            return False  # Fail open — don't block scans on regime check failure

    def _validate_signal(self, signal: dict, market: str) -> list[str]:
        """Validate agent output structure. Returns list of issues (empty = valid)."""
        issues = []

        for field in _REQUIRED_SIGNAL_FIELDS:
            if field not in signal or signal[field] is None:
                issues.append(f"missing field: {field}")

        if not issues:
            # Confidence range
            conf = signal.get("confidence_score")
            if not isinstance(conf, (int, float)) or conf < 0 or conf > 100:
                issues.append(f"confidence_score must be 0-100, got {conf}")

            # Direction enum
            direction = signal.get("direction", "").upper()
            if direction not in _VALID_DIRECTIONS:
                issues.append(f"invalid direction: {direction}")

            # Price sanity
            for price_field in ("entry_price", "target_price", "stop_loss_price"):
                val = signal.get(price_field)
                if val is not None and val <= 0:
                    issues.append(f"{price_field} must be positive, got {val}")

        return issues

    async def scan_portfolio(self, positions: list[dict]) -> dict:
        """Scan all portfolio positions in parallel.

        Args:
            positions: list of {"market": str, "ticker": str}
        """
        if not positions:
            return {"results": [], "errors": []}

        tasks = [self.scan_single(p["market"], p["ticker"]) for p in positions]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        ok, errors = [], []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                errors.append({"ticker": positions[i]["ticker"], "error": str(r)})
            elif r.get("error"):
                errors.append({"ticker": positions[i]["ticker"], "error": r["error"]})
            else:
                ok.append(r)

        return {"results": ok, "errors": errors}

    async def scan_single(self, market: str, ticker: str) -> dict:
        """Scan a single ticker (for ad-hoc Telegram commands)."""
        # Emergency stop gate
        from src.risk import emergency
        if await emergency.is_active():
            logger.warning("scan_single_blocked_emergency_stop", ticker=ticker)
            return {"error": "Trading halted — emergency stop is active"}

        agents = {"IDX": self.idx_agent, "US": self.us_agent, "ETF": self.etf_agent}
        agent = agents.get(market)
        if not agent:
            return {"error": f"Unknown market: {market}"}

        # Sanitize ticker for LLM prompt — strip any control chars or injection attempts
        safe_ticker = ''.join(c for c in ticker if c.isalnum() or c in '.-')[:20]
        result = await agent.run(f"Analyze {safe_ticker} for trading opportunities right now.")

        # Validate signal before persisting
        if not result.get("error"):
            issues = self._validate_signal(result, market)
            if issues:
                logger.warning("invalid_signal_single", ticker=ticker, issues=issues)
                result["validation_issues"] = issues
            else:
                await self._save_signal(result)

        return result

    async def _save_signal(self, signal_data: dict):
        """Save a signal to the database with IDX order validation."""
        try:
            from src.models.database import async_session
            from src.models.tables import Signal
            from datetime import datetime, timezone, timedelta

            # IDX-specific order validation (including ADV liquidity gate)
            if signal_data.get("market") == "IDX":
                try:
                    from src.risk.idx_limits import validate_order
                    price = signal_data.get("entry_price")
                    prev_close = signal_data.get("prev_close")
                    lots = signal_data.get("suggested_lots", 1)
                    adv_20d = signal_data.get("adv_20d")  # 20-day avg volume in shares
                    if price and prev_close and lots:
                        validate_order(
                            signal_data.get("ticker", "?"),
                            float(price),
                            float(prev_close),
                            int(lots),
                            adv_20d=float(adv_20d) if adv_20d else None,
                        )
                except ValueError as e:
                    logger.warning("idx_order_validation_failed", ticker=signal_data.get("ticker"), error=str(e))
                    signal_data["validation_note"] = str(e)

            async with async_session() as session:
                signal = Signal(
                    ticker=signal_data.get("ticker"),
                    market=signal_data.get("market"),
                    strategy=signal_data.get("strategy", "Unknown"),
                    direction=signal_data.get("direction", "LONG"),
                    confidence_score=signal_data.get("confidence_score", 0),
                    entry_price=signal_data.get("entry_price"),
                    target_price=signal_data.get("target_price"),
                    stop_loss_price=signal_data.get("stop_loss_price"),
                    risk_reward_ratio=signal_data.get("risk_reward_ratio"),
                    reasoning=signal_data.get("reasoning"),
                    status="PENDING",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                )
                session.add(signal)
                await session.commit()
                logger.info("signal_saved", ticker=signal_data.get("ticker"))
        except Exception as e:
            logger.error("signal_save_failed", error=str(e))

    async def analyze_portfolio(self, portfolio_data: dict) -> dict:
        """Run deep analysis on the current portfolio holdings."""
        prompt = f"Analyze this portfolio and provide insights:\n{json.dumps(portfolio_data, indent=2)}"
        return await self.portfolio_analyst.run(prompt)
