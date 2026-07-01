"""Karsa Trading System - Lead Orchestrator"""

import asyncio
import json
import time
from decimal import Decimal

from src.agents.base import BaseAgent
from src.agents.idx_analyst import IDXAnalyst
from src.agents.us_analyst import USAnalyst
from src.agents.etf_analyst import ETFAnalyst
from src.agents.portfolio_analyst import PortfolioAnalyst
from src.agents.crypto_analyst import CryptoAnalyst
from src.data.mcp_client import MCPClient
from src.data.cache import CacheManager
from src.utils.rate_limit import RateLimiter
from src.utils.logging import get_logger
from src.config import settings

logger = get_logger("orchestrator")

ROUTINE_COMBO = settings.NROUTER_MODEL or "karsa-routine"

IDX_UNIVERSE = [
    # Banking
    "BBCA", "BBRI", "BMRI", "BBNI", "BRIS",
    # Telco
    "TLKM", "EXCL", "ISAT",
    # Consumer
    "UNVR", "ICBP", "KLBF", "HMSP",
    # Auto & Industrial
    "ASII", "SMSM",
    # Energy & Mining
    "PGAS", "ADRO", "ITMG", "PTBA",
    # Tech
    "GOTO", "BUKA", "EMTEK",
    # Infra & Property
    "JSMR", "WIKA",
    # Healthcare
    "MIKA", "HEAL",
    # Retail
    "MAPI", "ACES",
    # Plantation
    "LSIP", "AALI",
]

US_UNIVERSE = [
    # Mega-cap Tech
    "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA",
    # Semis & Healthcare
    "AVGO", "LLY", "AMD",
    # Financials & Industrial
    "JPM", "V", "UNH", "XOM", "COST",
]

ETF_UNIVERSE = [
    # Broad Market
    "SPY", "QQQ", "IWM",
    # Sector
    "XLF", "XLK", "XLV", "XLE", "XLI",
    # Commodities & Fixed Income
    "GLD", "TLT", "SLV",
    # International
    "EEM",
]

from src.advisory.crypto_universe import CRYPTO_UNIVERSE

# Signal deduplication cache: ticker+direction → last signal timestamp
_signal_cache: dict[str, float] = {}
_SIGNAL_DEDUP_SECONDS = 4 * 3600  # 4 hours

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

        self.crypto_agent = CryptoAnalyst(mcp, rate_limiter)
        self.crypto_agent.combo_name = ROUTINE_COMBO

        # Shared intelligence engine — reuse across scans to preserve in-memory cache
        from src.advisory.idx_intelligence import IDXMarketIntelligence
        self.idx_intel = IDXMarketIntelligence(mcp)

        # Crypto risk manager + SOR (lazy init — only if Bybit configured)
        self._crypto_risk_manager = None
        self._crypto_sor = None

    def _get_crypto_risk_manager(self):
        if self._crypto_risk_manager is None:
            from src.risk.crypto_risk_manager import CryptoRiskManager
            self._crypto_risk_manager = CryptoRiskManager(self.mcp)
        return self._crypto_risk_manager

    def _get_crypto_sor(self):
        if self._crypto_sor is None:
            bybit = self.mcp._get_bybit()
            from src.risk.sor import SmartOrderRouter
            self._crypto_sor = SmartOrderRouter(bybit)
        return self._crypto_sor

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
            # Composite gate: check IDX market intelligence before scanning
            idx_composite = None
            try:
                idx_composite = await self.idx_intel.get_regime_composite()
                score = idx_composite.get("score", 0)
                logger.info("idx_composite_score", score=score, state=idx_composite.get("state"))

                if score <= -50:
                    logger.warning("idx_scan_skipped_composite", score=score, state=idx_composite.get("state"))
                    # Don't add IDX task — skip entirely
                elif score <= -20:
                    logger.info("idx_scan_caution", score=score, reason="composite below -20")
                    tasks.append(self._scan_market("IDX", self.idx_agent, IDX_UNIVERSE, composite=idx_composite))
                else:
                    tasks.append(self._scan_market("IDX", self.idx_agent, IDX_UNIVERSE, composite=idx_composite))
            except Exception as e:
                logger.warning("idx_composite_check_failed", error=str(e))
                # Fail open — proceed with scan if composite check fails
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

        # CRYPTO: 24/7, no market-hours gate. Auto-execute after scan.
        crypto_signals = []
        if market_filter in (None, "CRYPTO") and settings.BYBIT_API_KEY:
            try:
                from src.advisory.crypto_regime import CryptoRegimeFilter
                crypto_regime_filter = CryptoRegimeFilter(self.mcp)
                crypto_regime = await crypto_regime_filter.get_current_regime()
                regime_state = crypto_regime.get("state", "UNKNOWN")
                logger.info("crypto_regime", state=regime_state, hurst=crypto_regime.get("hurst"), adx=crypto_regime.get("adx"),
                            btc_dom=crypto_regime.get("btc_dominance"), season=crypto_regime.get("market_season"))

                # Phase 1: Update agent strategy based on current regime
                strategy_config = self.crypto_agent.update_strategy(regime_state)
                logger.info("crypto_strategy_applied",
                            regime=regime_state,
                            strategy=strategy_config.get("primary_strategy"),
                            size_multiplier=strategy_config.get("size_multiplier"))

                if regime_state == "CHOP":
                    logger.info("crypto_scan_skipped_chop_regime")
                else:
                    # Parallel scanning: scan each pair concurrently (like IDX/US)
                    scan_result = await self._scan_crypto_parallel(crypto_regime)
                    # Auto-execute: risk check → SOR → save
                    if scan_result:
                        crypto_signals = await self._auto_execute_crypto(scan_result, crypto_regime)
            except Exception as e:
                logger.error("crypto_scan_failed", error=str(e))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_signals = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("market_scan_failed", error=str(result))
                continue
            all_signals.extend(result)

        # Include crypto signals (already auto-executed and saved by _auto_execute_crypto)
        all_signals.extend(crypto_signals)

        # Persist non-crypto signals to database (crypto already saved in _auto_execute_crypto)
        crypto_ids = {id(s) for s in crypto_signals}
        for signal in all_signals:
            if id(signal) not in crypto_ids:
                await self._save_signal(signal)

        logger.info("scan_complete", total=len(all_signals))
        return all_signals

    async def _scan_market(self, market: str, agent: BaseAgent, universe: list[str], composite: dict | None = None) -> list[dict]:
        signals = []

        # Build context hint from composite score (if available)
        context_hint = ""
        if composite and market == "IDX":
            score = composite.get("score", 0)
            state = composite.get("state", "UNKNOWN")
            triggers = composite.get("triggers", [])
            context_hint = (
                f"\n\nMARKET CONTEXT: IDX composite score is {score:+.0f}/100 ({state}). "
                + (f"Key triggers: {'; '.join(triggers[:3])}. " if triggers else "")
                + "Factor this into your confidence scoring."
            )

        for ticker in universe:
            # Per-ticker emergency check — allows partial completion if stop activates mid-scan
            from src.risk import emergency
            if await emergency.is_active():
                logger.warning("scan_aborted_emergency_stop", market=market, ticker=ticker)
                break

            try:
                from src.utils.validation import sanitize_for_prompt
                safe_ticker = sanitize_for_prompt(ticker)
                prompt = f"Analyze {safe_ticker} for trading opportunities right now.{context_hint}"
                result = await agent.run(prompt)
                if result.get("error"):
                    logger.warning("agent_error", market=market, ticker=ticker, error=result["error"])
                    continue

                # Phase 2: Extract and save reasoning trace (if captured)
                trace_data = result.pop("_trace", None)

                # Validate signal structure
                issues = self._validate_signal(result, market)
                if issues:
                    logger.warning("invalid_signal", market=market, ticker=ticker, issues=issues)
                    continue

                if result.get("confidence_score", 0) >= 50:
                    signals.append(result)

                # Save trace for all crypto signals (even low confidence)
                if trace_data and market == "CRYPTO":
                    await self._save_trace(trace_data, result)
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

    async def _scan_crypto_parallel(self, regime: dict | None = None) -> list[dict]:
        """Scan all crypto pairs in parallel using asyncio.gather.

        Each pair runs independently. Results are merged and deduplicated.
        Dedup: skip if same ticker+direction signal exists within 4 hours.
        """
        global _signal_cache
        now = time.time()

        # Clean expired dedup entries
        expired = [k for k, v in _signal_cache.items() if now - v > _SIGNAL_DEDUP_SECONDS]
        for k in expired:
            del _signal_cache[k]

        # Scan each pair concurrently
        tasks = [
            self._scan_market("CRYPTO", self.crypto_agent, [symbol], composite=regime)
            for symbol in CRYPTO_UNIVERSE
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        signals = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("crypto_pair_scan_failed", error=str(result))
                continue
            if not result:
                continue

            for signal in result:
                ticker = signal.get("ticker", "")
                direction = signal.get("direction", "")
                dedup_key = f"{ticker}:{direction}"

                if dedup_key in _signal_cache:
                    logger.info("crypto_signal_deduped", ticker=ticker, direction=direction)
                    continue

                _signal_cache[dedup_key] = now
                signals.append(signal)

        return signals

    async def _auto_execute_crypto(self, signals: list[dict], regime: dict | None = None) -> list[dict]:
        """Risk-check and auto-execute crypto signals on Bybit testnet."""
        risk_mgr = self._get_crypto_risk_manager()
        sor = self._get_crypto_sor()
        executed = []

        # Get current positions and wallet balance
        bybit = self.mcp._get_bybit()
        open_positions = await bybit.get_positions()
        wallet = await bybit.get_wallet_balance()
        balance = wallet.get("balance", 0)

        if balance <= 0:
            logger.warning("crypto_wallet_empty", available=wallet.get("available", 0))
            return signals

        # Fix #3: Compute today's realized P&L for daily loss limit
        daily_pnl_pct = 0.0
        try:
            from src.models.database import async_session
            from src.models.tables import ClosedPaperTrade
            from sqlalchemy import select, func, cast, Date
            from datetime import datetime, timezone
            async with async_session() as session:
                today = datetime.now(timezone.utc).date()
                result = await session.execute(
                    select(func.sum(ClosedPaperTrade.realized_pnl_pct))
                    .where(
                        ClosedPaperTrade.market == "CRYPTO",
                        cast(ClosedPaperTrade.exit_date, Date) == today,
                    )
                )
                daily_pnl_pct = result.scalar() or 0.0
        except Exception as e:
            logger.debug("daily_pnl_query_failed", error=str(e))

        for signal in signals:
            ticker = signal.get("ticker", "?")

            # Fix #4: Check emergency halt between each signal execution
            from src.risk import emergency
            if await emergency.is_active():
                logger.warning("crypto_execution_halted_emergency", ticker=ticker)
                signal["status"] = "HALTED"
                executed.append(signal)
                break

            # Risk evaluation (with daily P&L)
            risk_result = await risk_mgr.evaluate(
                signal=signal,
                open_positions=open_positions,
                wallet_balance=balance,
                regime=regime,
                daily_pnl_pct=daily_pnl_pct,
            )

            if not risk_result.get("approved"):
                logger.info("crypto_signal_rejected", ticker=ticker, reason=risk_result.get("reason"))
                signal["status"] = "REJECTED"
                signal["rejection_reason"] = risk_result.get("reason")
                executed.append(signal)
                continue

            # Auto-execute via SOR
            fill_result = await sor.execute_order(signal, risk_result)

            if fill_result.get("success"):
                signal["status"] = "EXECUTED"
                signal["fill_price"] = fill_result.get("fill_price")
                signal["qty"] = risk_result.get("qty")
                signal["stop_loss"] = risk_result.get("stop_loss")
                signal["take_profit"] = risk_result.get("take_profit")
                signal["risk_amount"] = risk_result.get("risk_amount")
                signal["order_id"] = fill_result.get("order_id")
                logger.info(
                    "crypto_auto_executed",
                    ticker=ticker,
                    side=signal.get("direction"),
                    qty=risk_result.get("qty"),
                    fill=fill_result.get("fill_price"),
                    risk=risk_result.get("risk_amount"),
                )
                # Persist to CryptoPosition table (survives API outages)
                await self._save_crypto_position(signal, risk_result, fill_result)
                # Send Telegram notification
                await self._notify_crypto_trade(signal, risk_result)

                # Fix #2: Track new position in-loop to prevent duplicate entries
                open_positions.append({"symbol": ticker, "ticker": ticker})
            else:
                signal["status"] = "EXECUTION_FAILED"
                signal["execution_error"] = fill_result.get("error")
                logger.warning("crypto_execution_failed", ticker=ticker, error=fill_result.get("error"))

            # Save signal to DB regardless
            await self._save_signal(signal)
            executed.append(signal)

        return executed

    async def _save_crypto_position(self, signal: dict, risk_result: dict, fill_result: dict):
        """Persist executed crypto trade to CryptoPosition table."""
        try:
            from src.models.tables import CryptoPosition
            from datetime import datetime, timezone

            # Fetch entry funding rate and regime for metadata
            entry_funding_rate = None
            regime_at_entry = None
            try:
                from src.risk.funding_tracker import FundingTracker
                bybit = self.mcp._get_bybit()
                ft = FundingTracker(bybit)
                rates = await ft.get_current_rates([signal.get("ticker", "")])
                if rates:
                    entry_funding_rate = rates[0].get("funding_rate")
            except Exception:
                pass
            try:
                from src.advisory.crypto_regime import CryptoRegimeFilter
                crf = CryptoRegimeFilter(self.mcp)
                regime_data = await crf.get_current_regime()
                regime_at_entry = regime_data.get("state") if regime_data else None
            except Exception:
                pass

            entry_price = Decimal(str(fill_result.get("fill_price", signal.get("entry_price", 0))))

            async with async_session() as session:
                session.add(CryptoPosition(
                    ticker=signal.get("ticker", ""),
                    side="Buy" if signal.get("direction") == "LONG" else "Sell",
                    size=risk_result.get("qty", 0),
                    entry_price=entry_price,
                    leverage=risk_result.get("leverage", 1),
                    liquidation_price=None,  # populated by position sync job
                    stop_loss=risk_result.get("stop_loss"),
                    take_profit=risk_result.get("take_profit"),
                    # Phase 1: lifecycle metadata
                    highest_price=entry_price,
                    trailing_stop_price=None,  # set by trailing stop manager
                    entry_funding_rate=entry_funding_rate,
                    regime_at_entry=regime_at_entry,
                    signal_source=signal.get("strategy", "crypto_analyst"),
                    partial_exits_taken=0,
                    last_management_check=datetime.now(timezone.utc),
                ))
                await session.commit()
        except Exception as e:
            logger.error("crypto_position_save_failed", error=str(e))

    async def _notify_crypto_trade(self, signal: dict, risk_result: dict):
        """Send Telegram notification for an executed crypto trade."""
        try:
            import httpx
            from src.utils.trader_format import signal_card
            
            ticker = signal.get("ticker", "?")
            direction = signal.get("direction", "?")
            confidence = float(signal.get("confidence_score", 0))
            entry = float(signal.get("fill_price") or signal.get("entry_price") or 0.0)
            sl = float(risk_result.get("stop_loss") or signal.get("stop_loss") or 0.0)
            tp = float(risk_result.get("take_profit") or signal.get("take_profit") or 0.0)
            reasoning = signal.get("reasoning", "No thesis provided.")

            msg = str(signal_card(
                ticker=ticker,
                direction=direction,
                confidence=confidence,
                entry=entry,
                sl=sl,
                tp=tp,
                reasoning=reasoning
            ))
            # Use the crypto telegram bot token (falls back to main token) for notifications
            token = settings.CRYPTO_TELEGRAM_TOKEN or settings.TELEGRAM_TOKEN
            chat_id = settings.CRYPTO_TELEGRAM_CHAT_ID or settings.TELEGRAM_CHAT_ID
            if token and chat_id:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                    )
        except Exception as e:
            logger.error("crypto_notify_failed", error=str(e))

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

        agents = {"IDX": self.idx_agent, "US": self.us_agent, "ETF": self.etf_agent, "CRYPTO": self.crypto_agent}
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

        # Auto-execute crypto signals (same path as scan_all_markets)
        if market == "CRYPTO" and not result.get("error") and result.get("confidence_score", 0) >= 50:
            try:
                from src.advisory.crypto_regime import CryptoRegimeFilter
                regime_filter = CryptoRegimeFilter(self.mcp)
                regime = await regime_filter.get_current_regime()
                # Phase 1: Update strategy for single-ticker scan too
                self.crypto_agent.update_strategy(regime.get("state", "UNKNOWN"))
                executed = await self._auto_execute_crypto([result], regime)
                if executed:
                    return executed[0]
            except Exception as e:
                logger.error("crypto_auto_execute_single_failed", ticker=ticker, error=str(e))

        return result

    async def _save_trace(self, trace_data: dict, signal_data: dict):
        """Save a reasoning trace to the database."""
        if not trace_data:
            return
        try:
            from src.models.database import async_session
            from src.models.tables import ReasoningTrace
            async with async_session() as session:
                trace = ReasoningTrace(
                    agent_name=trace_data.get("agent_name", ""),
                    ticker=signal_data.get("ticker"),
                    market=signal_data.get("market"),
                    system_prompt=trace_data.get("system_prompt", ""),
                    user_prompt=trace_data.get("user_prompt", ""),
                    tools_used=trace_data.get("tools_used"),
                    tool_results=trace_data.get("tool_results"),
                    llm_response=trace_data.get("llm_response"),
                    reasoning_extracted=trace_data.get("reasoning"),
                    strategy_used=trace_data.get("strategy"),
                    regime_at_time=getattr(self.crypto_agent, '_current_config', {}).get("primary_strategy"),
                    confidence_score=trace_data.get("confidence"),
                    iterations=trace_data.get("iterations", 1),
                    model_used=trace_data.get("model"),
                )
                session.add(trace)
                await session.commit()
        except Exception as e:
            logger.error("trace_save_failed", error=str(e))

    async def _save_signal(self, signal_data: dict):
        """Save a signal to the database with IDX order validation."""
        try:
            from src.models.database import async_session
            from src.models.tables import Signal
            from datetime import datetime, timedelta

            # IDX-specific order validation (including ADV liquidity gate)
            if signal_data.get("market") == "IDX":
                try:
                    from src.risk.idx_limits import validate_order, ihsg_circuit_breaker_level, check_forced_sell_triggers
                    ticker = signal_data.get("ticker", "?")

                    # Forced sell trigger check
                    try:
                        fs = check_forced_sell_triggers(ticker, market_data={
                            "adv_20d": signal_data.get("adv_20d"),
                            "current_volume": signal_data.get("volume"),
                        })
                        if fs.get("triggered"):
                            logger.warning("idx_forced_sell_trigger", ticker=ticker, rule=fs["rule_id"])
                            signal_data["validation_note"] = fs["description"]
                    except Exception as fs_err:
                        logger.debug("forced_sell_check_skipped", error=str(fs_err))

                    # Standard order validation with dynamic ARA/ARB
                    price = signal_data.get("entry_price")
                    prev_close = signal_data.get("prev_close")
                    lots = signal_data.get("suggested_lots", 1)
                    adv_20d = signal_data.get("adv_20d")  # 20-day avg volume in shares
                    if price and prev_close and lots:
                        validate_order(
                            ticker,
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
                    status=signal_data.get("status", "PENDING"),
                    expires_at=datetime.utcnow() + timedelta(hours=24),
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
