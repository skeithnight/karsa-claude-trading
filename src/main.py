"""Karsa Trading System - Entry Point & Scheduler"""

# asyncpg monkey-patch is applied in src/models/database.py at import time.
# Do NOT duplicate it here — see docs/DATABASE_AUDIT.md Finding 1.

import asyncio
import signal
import sys

import src.patch_websocket  # noqa: F401 - Must be imported first to apply patches

import redis.asyncio as redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from fastapi import FastAPI
import uvicorn

from src.config import settings
from src.models.database import init_db, close_db, async_session
from src.data.cache import CacheManager
from src.data.mcp_client import MCPClient
from src.agents.orchestrator import Orchestrator
from src.utils.logging import setup_logging, get_logger
from src.utils.rate_limit import RateLimiter

logger = get_logger("main")

from src.utils.position_snapshot import snapshot_from_db as _snapshot_position

# FastAPI app for health endpoints
app = FastAPI(title="Karsa Orchestrator", version="0.1.0")

# Module-level reference for API routes
karsa_app: "KarsaApp | None" = None

class KarsaApp:
    """Main application container with APScheduler integration."""

    def __init__(self):
        self.redis_client: redis.Redis | None = None
        self.cache: CacheManager | None = None
        self.mcp: MCPClient | None = None
        self.rate_limiter: RateLimiter | None = None
        self.orchestrator: Orchestrator | None = None
        self.scheduler: AsyncIOScheduler | None = None
        self._shutdown = asyncio.Event()

    async def startup(self):
        """Initialize all services and register scheduled jobs."""
        logger.info("starting_karsa", version="0.1.0")

        await init_db()
        logger.info("database_ready")

        self.redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.cache = CacheManager(self.redis_client)
        self.rate_limiter = RateLimiter(self.redis_client)

        if await self.cache.ping():
            logger.info("redis_ready")

        else:
            logger.error("redis_connection_failed")
            sys.exit(1)

        # Event Bus (architecture Phase 2 — shadow mode, no behavioral change)
        from src.architecture.events import event_bus as _event_bus
        from src.architecture.feature_flags import flags as _arch_flags
        _arch_flags.set_redis(self.redis_client)
        _event_bus.set_redis(self.redis_client)
        await _event_bus.start()
        from src.metrics.crypto_metrics import EVENT_BUS_ACTIVE
        EVENT_BUS_ACTIVE.set(1)
        from src.architecture.events.subscribers import metrics_subscriber, journal_subscriber
        _event_bus.subscribe("PositionReduced", metrics_subscriber)
        _event_bus.subscribe("PositionClosed", metrics_subscriber)
        _event_bus.subscribe("TrailingActivated", metrics_subscriber)
        _event_bus.subscribe("BreakEvenActivated", metrics_subscriber)
        _event_bus.subscribe("StopLossTriggered", metrics_subscriber)
        _event_bus.subscribe("PositionReduced", journal_subscriber)
        _event_bus.subscribe("PositionClosed", journal_subscriber)
        _event_bus.subscribe("TrailingActivated", journal_subscriber)
        _event_bus.subscribe("BreakEvenActivated", journal_subscriber)
        _event_bus.subscribe("StopLossTriggered", journal_subscriber)

        # Exit Engine (Phase 4 — single exit authority)
        from src.architecture.exit import ExitEngine, EmergencyExitStrategy, StopLossStrategy, TimeExitStrategy, TrailingStopStrategy, PartialExitStrategy, BreakEvenStrategy
        self.exit_engine = ExitEngine()
        self.exit_engine.register(EmergencyExitStrategy())
        self.exit_engine.register(StopLossStrategy())
        self.exit_engine.register(TimeExitStrategy())
        self.exit_engine.register(TrailingStopStrategy())
        self.exit_engine.register(PartialExitStrategy())
        self.exit_engine.register(BreakEvenStrategy())
        logger.info("exit_engine_ready", strategies=[s.name for s in self.exit_engine._strategies])

        self.mcp = MCPClient(self.cache)

        # Agents & orchestrator
        self.orchestrator = Orchestrator(self.mcp, self.cache, self.rate_limiter)

        # Risk profile + dynamic universe engine
        from src.risk.profile_manager import RiskProfileManager
        from src.advisory.crypto_universe import UniverseEngine
        self.profile_manager = RiskProfileManager(self.redis_client)
        await self.profile_manager.ensure_default()
        self.orchestrator.profile_manager = self.profile_manager
        bybit = self.mcp._get_bybit()
        self.universe_engine = UniverseEngine(bybit, self.redis_client, self.profile_manager)
        self.orchestrator.universe_engine = self.universe_engine

        # Decision Engine (Phase 5)
        from src.architecture.decision import DecisionEngine, AnalyzerSource, PolicySource
        self.decision_engine = DecisionEngine()
        self.decision_engine.add_source(AnalyzerSource())
        self.decision_engine.add_source(PolicySource())
        self.orchestrator.decision_engine = self.decision_engine
        logger.info("decision_engine_ready", sources=[s.name for s in self.decision_engine._sources])

        # Policy Engine (Phase 7)
        from src.architecture.policy import PolicyEngine, TradingPolicy, RiskPolicy, EmergencyPolicy
        self.policy_engine = PolicyEngine()
        self.policy_engine.add_policy(EmergencyPolicy.kill_switch())
        self.policy_engine.add_policy(EmergencyPolicy.circuit_breaker())
        self.policy_engine.add_policy(RiskPolicy.daily_loss_limit())
        self.policy_engine.add_policy(RiskPolicy.max_leverage())
        self.policy_engine.add_policy(TradingPolicy.trading_mode_check())
        self.policy_engine.add_policy(TradingPolicy.max_positions_check())
        self.orchestrator.policy_engine = self.policy_engine
        logger.info("policy_engine_ready", policies=[p["name"] for p in self.policy_engine.list_policies()])

        # Agent Runtime (Phase 8)
        from src.architecture.agent_runtime import AgentRuntime, AgentRegistry, AgentConfig
        self.agent_runtime = AgentRuntime(max_concurrent=5)
        self.agent_registry = AgentRegistry()
        self.agent_registry.register(AgentConfig("crypto_analyst", max_retries=3, timeout_seconds=120, combo_name="karsa-routine"))
        self.agent_registry.register(AgentConfig("crypto_auditor", max_retries=2, timeout_seconds=60, combo_name="karsa-routine"))
        self.orchestrator.agent_runtime = self.agent_runtime
        logger.info("agent_runtime_ready", agents=self.agent_registry.all_types())

        # Execution engine modules
        from src.execution.websocket_manager import WebSocketManager
        from src.execution.sl_engine import StopLossEngine
        from src.execution.oms import OrderManagementSystem
        self.ws_manager = WebSocketManager(self.redis_client, bybit)
        self.sl_engine = StopLossEngine(self.redis_client, bybit)
        self.oms = OrderManagementSystem(self.redis_client, bybit)

        # Confidence calibration
        from src.risk.calibration_engine import ConfidenceCalibrator
        self.calibrator = ConfidenceCalibrator()
        self.orchestrator.calibrator = self.calibrator

        # Register Prometheus metrics (must import at startup so prometheus_client sees them)
        import src.metrics.crypto_metrics  # noqa: F401

        logger.info("orchestrator_ready")

        # APScheduler with in-memory job store
        jobstores = {
            "default": MemoryJobStore(),
        }
        self.scheduler = AsyncIOScheduler(jobstores=jobstores)
        self._register_jobs()
        self.scheduler.start()
        logger.info("scheduler_started")

        # Wire health endpoints + API routes into FastAPI
        self._register_health_routes()
        self._register_api_routes()

        # Set module-level reference for API routes
        global karsa_app
        karsa_app = self

        logger.info("karsa_ready")

    def _register_health_routes(self):
        """Register health check endpoints on the FastAPI app."""

        @app.get("/health")
        async def health():
            db_ok = False
            db_error = None
            try:
                from sqlalchemy import text
                async with async_session() as session:
                    await session.execute(text("SELECT 1"))
                    db_ok = True
            except Exception as e:
                db_error = str(e)

            redis_ok = await self.cache.ping() if self.cache else False

            return {
                "status": "ok" if (db_ok and redis_ok) else "degraded",
                "trading_mode": settings.TRADING_MODE,
                "checks": {
                    "postgres": "ok" if db_ok else f"FAIL: {db_error}",
                    "redis": "ok" if redis_ok else "FAIL",
                },
            }

        @app.get("/health/scheduler")
        async def scheduler_status():
            if not self.scheduler:
                return {"status": "not_initialized", "jobs": []}

            jobs = []
            for job in self.scheduler.get_jobs():
                next_run = job.next_run_time.isoformat() if job.next_run_time else None
                jobs.append({"id": job.id, "name": job.name, "next_run": next_run})

            return {
                "status": "running" if self.scheduler.running else "stopped",
                "jobs": jobs,
                "job_count": len(jobs),
            }

    def _register_api_routes(self):
        """Register REST API routes and /metrics endpoint."""
        from src.api.routes import router as api_router
        app.include_router(api_router)

        @app.get("/metrics")
        async def metrics():
            import src.metrics.crypto_metrics  # ensure registered
            from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
            from fastapi.responses import Response
            return Response(
                content=generate_latest(),
                media_type=CONTENT_TYPE_LATEST,
            )

    def _register_jobs(self):
        """Register all periodic jobs.

        Market Hours (UTC):
        - IDX: 09:00-16:00 WIB = 02:00-09:00 UTC (lunch 12:00-13:30 WIB = 05:00-06:30 UTC)
        - US: 09:30-16:00 ET = 13:30-20:00 UTC
        """
        s = self.scheduler

        # --- IDX MARKET ---
        # Pre-open scan: 08:55 WIB (01:55 UTC) — catch overnight moves (skip market-hours gate)
        async def _scan_idx_preopen():
            await self._job_scan_idx(preopen=True)

        s.add_job(
            _scan_idx_preopen,
            "cron", day_of_week="mon-fri", hour=1, minute=55,
            id="scan_idx_preopen", name="IDX Pre-Open Scan (08:55 WIB)",
            replace_existing=True, misfire_grace_time=300,
        )

        # Mid-session scans: 10:00-12:00 WIB (03:00-05:00 UTC)
        s.add_job(
            self._job_scan_idx,
            "cron", day_of_week="mon-fri", hour="3-5", minute="0,30",
            id="scan_idx_morning", name="IDX Market Scan (Morning)",
            replace_existing=True, misfire_grace_time=300,
        )

        # Afternoon scans: 13:30-15:00 WIB (06:30-08:00 UTC)
        s.add_job(
            self._job_scan_idx,
            "cron", day_of_week="mon-fri", hour="6-8", minute="0,30",
            id="scan_idx_afternoon", name="IDX Market Scan (Afternoon)",
            replace_existing=True, misfire_grace_time=300,
        )

        # Pre-close scan: 14:45 WIB (07:45 UTC) — catch end-of-day positioning
        s.add_job(
            self._job_scan_idx,
            "cron", day_of_week="mon-fri", hour=7, minute=45,
            id="scan_idx_preclose", name="IDX Pre-Close Scan (14:45 WIB)",
            replace_existing=True, misfire_grace_time=300,
        )

        s.add_job(
            self._job_eod_review,
            "cron", day_of_week="mon-fri", hour=9, minute=15,
            id="eod_review_idx", name="IDX EOD Review",
            replace_existing=True, misfire_grace_time=600,
        )

        # --- US MARKET ---
        s.add_job(
            self._job_scan_us_etf,
            "cron", day_of_week="mon-fri", hour="13-19", minute="0,30",
            id="scan_us_etf", name="US & ETF Market Scan",
            replace_existing=True, misfire_grace_time=300,
        )

        s.add_job(
            self._job_premarket_battleplan,
            "cron", day_of_week="mon-fri", hour=14, minute=25,
            id="premarket_battleplan", name="Pre-Market Battle Plan",
            replace_existing=True, misfire_grace_time=600,
        )

        s.add_job(
            self._job_eod_review,
            "cron", day_of_week="mon-fri", hour=21, minute=15,
            id="eod_review_us", name="US EOD Review",
            replace_existing=True, misfire_grace_time=600,
        )

        # --- SHARED ---
        s.add_job(
            self._job_update_paper_positions,
            "cron", day_of_week="mon-fri", hour="2-9,13-20", minute="*/5",
            id="paper_update", name="Paper Position Price Update",
            replace_existing=True, misfire_grace_time=120,
        )

        s.add_job(
            self._job_kill_switch,
            "cron", day_of_week="mon-fri", hour="2-9,13-20", minute="*/5",
            id="kill_switch", name="Daily Loss Kill Switch",
            replace_existing=True, misfire_grace_time=60,
        )

        s.add_job(
            self._job_flush_cache,
            "cron", minute=5,
            id="flush_cache", name="Flush OHLCV Cache",
            replace_existing=True,
        )

        # --- CRYPTO (24/7, no weekday/hour gate) ---
        if settings.BYBIT_API_KEY:
            s.add_job(
                self._job_scan_crypto,
                "cron", hour="*", minute=15,
                id="scan_crypto", name="Crypto Market Scan (24/7)",
                replace_existing=True, misfire_grace_time=600,
            )
            # Refresh dynamic universe every 15 minutes
            s.add_job(
                self._job_refresh_universe,
                "cron", minute="*/15",
                id="refresh_universe", name="Crypto Universe Refresh (every 15m)",
                replace_existing=True, misfire_grace_time=600,
            )
            # Monitor open positions every 15 minutes
            s.add_job(
                self._job_monitor_crypto_positions,
                "cron", minute="*/15",
                id="crypto_monitor", name="Crypto Position Monitor",
                replace_existing=True, misfire_grace_time=120,
            )
            # Sync funding rates at 00:00, 08:00, 16:00 UTC (Bybit funding times)
            s.add_job(
                self._job_sync_crypto_funding,
                "cron", hour="0,8,16", minute=5,
                id="crypto_funding", name="Crypto Funding Rate Sync",
                replace_existing=True, misfire_grace_time=300,
            )
            # Daily PnL snapshot at midnight UTC
            s.add_job(
                self._job_crypto_pnl_snapshot,
                "cron", hour=0, minute=0,
                id="crypto_pnl_snapshot", name="Crypto Daily PnL Snapshot",
                replace_existing=True, misfire_grace_time=600,
            )
            # Sync Bybit positions to local DB every 5 minutes
            s.add_job(
                self._job_sync_crypto_positions,
                "cron", minute="*/5",
                id="crypto_position_sync", name="Crypto Position Sync",
                replace_existing=True, misfire_grace_time=120,
            )

            # --- Phase 1: Lifecycle management jobs ---
            # Trailing stops: adjust stops for winning positions every 5 min
            s.add_job(
                self._job_update_trailing_stops,
                "cron", minute="*/5",
                id="crypto_trailing_stops", name="Crypto Trailing Stop Update",
                replace_existing=True, misfire_grace_time=120,
            )
            # Partial exits: scale out at profit targets every 2 min
            s.add_job(
                self._job_check_partial_exits,
                "cron", minute="*/2",
                id="crypto_partial_exits", name="Crypto Partial Exit Check",
                replace_existing=True, misfire_grace_time=60,
            )
            # Time-based exits: close stale positions hourly
            s.add_job(
                self._job_check_time_exits,
                "cron", hour="*", minute=30,
                id="crypto_time_exits", name="Crypto Time-Based Exit Check",
                replace_existing=True, misfire_grace_time=300,
            )
            # Circuit breakers: vol spike + correlation cascade every 1 min
            s.add_job(
                self._job_check_circuit_breakers,
                "cron", minute="*/1",
                id="crypto_circuit_breakers", name="Crypto Circuit Breaker Check",
                replace_existing=True, misfire_grace_time=60,
            )
            # Cumulative funding enforcement hourly
            s.add_job(
                self._job_enforce_funding_limit,
                "cron", hour="*", minute=20,
                id="crypto_funding_limit", name="Crypto Funding Limit Enforcement",
                replace_existing=True, misfire_grace_time=300,
            )
            # Position reconciliation every 60s (exchange as source of truth)
            s.add_job(
                self._job_reconcile_positions,
                "interval", seconds=60,
                id="crypto_reconciliation", name="Crypto Position Reconciliation",
                replace_existing=True, misfire_grace_time=30,
            )
            # Liquidity check every 15 min (top 3 pairs)
            s.add_job(
                self._job_liquidity_check,
                "cron", minute="*/15",
                id="crypto_liquidity", name="Crypto Liquidity Check",
                replace_existing=True, misfire_grace_time=120,
            )
            # OMS stuck order cleanup every 2 min
            s.add_job(
                self._job_oms_cleanup,
                "interval", minutes=2,
                id="oms_cleanup", name="OMS Stuck Order Cleanup",
                replace_existing=True, misfire_grace_time=60,
            )

        # Metrics sync every 1 min (infra health gauges)
        s.add_job(
            self._job_metrics_sync,
            "interval", minutes=1,
            id="metrics_sync", name="Metrics Health Sync",
            replace_existing=True, misfire_grace_time=30,
        )

        logger.info("jobs_registered", count=len(self.scheduler.get_jobs()))

    # --- Job implementations ---

    async def _job_scan_idx(self, preopen: bool = False):
        """Scan IDX market — full pipeline: agents → risk → persist → notify.

        Args:
            preopen: If True, skip market-hours gate (used for 08:55 WIB pre-open scan).
        """
        try:
            from src.utils.market_hours import is_idx_open
            if not preopen and not is_idx_open():
                logger.info("idx_market_closed_skip")
                return
            signals = await self.orchestrator.scan_all_markets("IDX")
            logger.info("idx_scan_done", signals=len(signals))
        except Exception as e:
            logger.error("idx_scan_failed", error=str(e))

    async def _job_scan_us_etf(self):
        """Scan US + ETF markets — full pipeline: agents → risk → persist → notify."""
        try:
            from src.utils.market_hours import is_us_open
            if not is_us_open():
                logger.info("us_market_closed_skip")
                return
            signals = await self.orchestrator.scan_all_markets("US_ETF")
            logger.info("us_etf_scan_done", signals=len(signals))
        except Exception as e:
            logger.error("us_etf_scan_failed", error=str(e))

    async def _job_scan_crypto(self):
        """Scan crypto market — auto-execute pipeline: agents → risk → SOR → save → notify."""
        try:
            signals = await self.orchestrator.scan_all_markets("CRYPTO")
            logger.info("crypto_scan_done", signals=len(signals))
        except Exception as e:
            logger.error("crypto_scan_failed", error=str(e))

    async def _job_refresh_universe(self):
        """Refresh dynamic crypto universe — score, rank, cache."""
        try:
            if self.universe_engine:
                universe = await self.universe_engine.generate()
                logger.info("universe_refreshed", count=len(universe))
        except Exception as e:
            logger.error("universe_refresh_failed", error=str(e))

    async def _job_monitor_crypto_positions(self):
        """Monitor open crypto positions — update P&L, alert on significant moves."""
        try:
            bybit = self.mcp._get_bybit()
            positions = await bybit.get_positions()

            if not positions:
                return

            from src.models.database import async_session
            from src.models.tables import PaperPosition
            from sqlalchemy import select

            alerts = []
            async with async_session() as session:
                for pos in positions:
                    symbol = pos.get("symbol", "")
                    pnl_pct = 0
                    entry = pos.get("entry_price", 0)
                    current = pos.get("current_price", 0)
                    if entry > 0:
                        if pos.get("side") == "Buy":
                            pnl_pct = ((current - entry) / entry) * 100
                        else:
                            pnl_pct = ((entry - current) / entry) * 100

                    # Update paper position in DB
                    result = await session.execute(
                        select(PaperPosition).where(
                            PaperPosition.ticker == symbol,
                            PaperPosition.market == "CRYPTO",
                        )
                    )
                    paper_pos = result.scalar_one_or_none()
                    if paper_pos:
                        paper_pos.current_price = current
                        paper_pos.unrealized_pnl = pos.get("unrealized_pnl", 0)
                        paper_pos.unrealized_pnl_pct = pnl_pct

                    # Alert on significant moves (thresholds account for leverage noise)
                    if pnl_pct <= -2.0:
                        alerts.append(f"⚠️ {symbol}: {pnl_pct:+.1f}% (${pos.get('unrealized_pnl', 0):+,.2f})")
                    elif pnl_pct >= 5.0:
                        alerts.append(f"🟢 {symbol}: {pnl_pct:+.1f}% consider taking profit")

                await session.commit()

            # Send Telegram alert if needed
            if alerts:
                try:
                    import httpx
                    token = settings.CRYPTO_TELEGRAM_TOKEN or settings.TELEGRAM_TOKEN
                    chat_id = settings.CRYPTO_TELEGRAM_CHAT_ID or settings.TELEGRAM_CHAT_ID
                    if token and chat_id:
                        msg = "📊 <b>CRYPTO POSITION UPDATE</b>\n\n" + "\n".join(alerts)
                        async with httpx.AsyncClient(timeout=10) as client:
                            await client.post(
                                f"https://api.telegram.org/bot{token}/sendMessage",
                                json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                            )
                except Exception:
                    pass

            logger.info("crypto_monitor_done", positions=len(positions), alerts=len(alerts))
        except Exception as e:
            logger.error("crypto_monitor_failed", error=str(e))

    async def _job_update_paper_positions(self):
        """Update current prices for paper positions AND portfolio."""
        logger.info("price_update_started")
        try:
            from src.models.tables import PaperPosition, PortfolioState
            from sqlalchemy import select

            async with async_session() as session:
                result = await session.execute(select(PaperPosition))
                positions = result.scalars().all()

                for pos in positions:
                    quote = await self.mcp.get_quote(pos.ticker, pos.market)
                    if quote and not quote.get("error"):
                        pos.current_price = quote.get("price")
                        if pos.entry_price and pos.current_price:
                            curr_p = float(pos.current_price)
                            entry_p = float(pos.entry_price)
                            qty = float(pos.quantity)
                            if pos.side == "LONG":
                                pos.unrealized_pnl = (curr_p - entry_p) * qty
                            else:
                                pos.unrealized_pnl = (entry_p - curr_p) * qty
                            pos.unrealized_pnl_pct = (float(pos.unrealized_pnl) / (entry_p * qty)) * 100

                port_result = await session.execute(select(PortfolioState))
                portfolio = port_result.scalars().all()

                for p in portfolio:
                    quote = await self.mcp.get_quote(p.ticker, p.market)
                    if quote and not quote.get("error"):
                        p.current_price = quote.get("price")
                        if p.avg_cost and p.current_price:
                            curr_p = float(p.current_price)
                            avg_c = float(p.avg_cost)
                            qty = float(p.quantity)
                            p.unrealized_pnl = (curr_p - avg_c) * qty

                await session.commit()
            logger.info("price_update_done", paper=len(positions), portfolio=len(portfolio))
        except Exception as e:
            logger.error("paper_update_failed", error=str(e))
    async def _job_premarket_battleplan(self):
        """Generate and push pre-market battle plan to Telegram."""
        logger.info("premarket_battleplan_started")
        # ponytail: call Orchestrator.generate_battleplan(), format, and send via bot_token/chat_id.
        logger.info("premarket_battleplan_done")

    async def _job_eod_review(self):
        """Generate and push EOD review to Telegram."""
        logger.info("eod_review_started")
        # ponytail: aggregate closed paper trades today, send summary to Telegram.
        logger.info("eod_review_done")

    async def _job_kill_switch(self):
        """Check if daily loss limit is breached — activate emergency stop if so."""
        logger.info("kill_switch_check_started")
        try:
            from src.models.tables import ClosedPaperTrade
            from src.risk import emergency
            from sqlalchemy import select, func, cast, Date
            from datetime import datetime, timezone

            async with async_session() as session:
                today = datetime.now(timezone.utc).date()
                result = await session.execute(
                    select(func.sum(ClosedPaperTrade.realized_pnl_pct))
                    .where(cast(ClosedPaperTrade.exit_date, Date) == today)
                )
                daily_pnl_pct = result.scalar() or 0.0

                if daily_pnl_pct <= -settings.CRYPTO_DAILY_LOSS_LIMIT_PCT:
                    activated = await emergency.activate(
                        reason=f"Daily loss limit breached: {daily_pnl_pct:+.2f}%",
                        operator="system-kill-switch",
                    )
                    if activated:
                        logger.warning("kill_switch_activated", daily_pnl_pct=daily_pnl_pct)
                        # Send Telegram alert
                        try:
                            import httpx
                            token = settings.CRYPTO_TELEGRAM_TOKEN or settings.TELEGRAM_TOKEN
                            chat_id = settings.CRYPTO_TELEGRAM_CHAT_ID or settings.TELEGRAM_CHAT_ID
                            async with httpx.AsyncClient(timeout=10) as client:
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/sendMessage",
                                    json={
                                        "chat_id": chat_id,
                                        "text": (
                                            "🚨 <b>KILL SWITCH ACTIVATED</b>\n"
                                            f"Daily P&amp;L: {daily_pnl_pct:+.2f}%\n"
                                            "All trading decisions are halted.\n"
                                            "Use /resume to reactivate."
                                        ),
                                        "parse_mode": "HTML",
                                    },
                                )
                        except Exception as e:
                            logger.error("kill_switch_telegram_failed", error=str(e))
        except Exception as e:
            logger.error("kill_switch_failed", error=str(e))

    async def _job_sync_crypto_funding(self):
        """Sync funding rate history from Bybit to DB."""
        try:
            bybit = self.mcp._get_bybit()
            from src.risk.funding_tracker import FundingTracker
            from src.models.tables import CryptoFundingPayment

            tracker = FundingTracker(bybit)
            async with async_session() as session:
                for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                               "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT"]:
                    records = await tracker.sync_funding_from_exchange(symbol)
                    for rec in records[-3:]:  # last 3 payments (24h)
                        from sqlalchemy import select
                        existing = await session.execute(
                            select(CryptoFundingPayment).where(
                                CryptoFundingPayment.ticker == symbol,
                                CryptoFundingPayment.funded_at == rec["funded_at"],
                            )
                        )
                        if existing.scalar_one_or_none() is None:
                            session.add(CryptoFundingPayment(
                                ticker=symbol,
                                funding_rate=rec["funding_rate"],
                                funding_fee=rec["funding_fee"],
                                position_size=rec["position_size"],
                                side=rec["side"],
                                funded_at=rec["funded_at"],
                            ))
                await session.commit()
            logger.info("crypto_funding_sync_done")
        except Exception as e:
            logger.error("crypto_funding_sync_failed", error=str(e))

    async def _job_crypto_pnl_snapshot(self):
        """Take daily PnL snapshot and persist to DB."""
        try:
            bybit = self.mcp._get_bybit()
            from src.models.tables import CryptoPnLSnapshot, ClosedPaperTrade, PaperPosition

            wallet = await bybit.get_wallet_balance()
            positions = await bybit.get_positions()
            unrealized = sum(p.get("unrealized_pnl", 0) for p in positions)

            async with async_session() as session:
                from sqlalchemy import select, func, cast, Date
                from datetime import datetime, timezone
                today = datetime.now(timezone.utc).date()
                realized_result = await session.execute(
                    select(func.sum(ClosedPaperTrade.realized_pnl))
                    .where(
                        ClosedPaperTrade.market == "CRYPTO",
                        cast(ClosedPaperTrade.exit_date, Date) == today,
                    )
                )
                realized = realized_result.scalar() or 0

                funding_result = await session.execute(
                    select(func.sum(CryptoPnLSnapshot.funding_costs))
                    .where(cast(CryptoPnLSnapshot.snapshot_date, Date) == today)
                )
                funding = funding_result.scalar() or 0

                session.add(CryptoPnLSnapshot(
                    snapshot_date=datetime.now(timezone.utc),
                    realized_pnl=realized,
                    unrealized_pnl=unrealized,
                    funding_costs=funding,
                    total_pnl=float(realized) + unrealized - float(funding),
                    equity=wallet.get("balance", 0),
                    open_positions=len(positions),
                ))
                await session.commit()
            logger.info("crypto_pnl_snapshot_done", equity=wallet.get("balance", 0))
        except Exception as e:
            logger.error("crypto_pnl_snapshot_failed", error=str(e))

    async def _job_sync_crypto_positions(self):
        """Sync Bybit positions to local CryptoPosition table."""
        try:
            from datetime import datetime as dt
            bybit = self.mcp._get_bybit()
            from src.models.tables import CryptoPosition

            positions = await bybit.get_positions()
            async with async_session() as session:
                from sqlalchemy import select
                for pos in positions:
                    symbol = pos.get("symbol", "")
                    existing = await session.execute(
                        select(CryptoPosition).where(
                            CryptoPosition.ticker == symbol,
                            CryptoPosition.status == "OPEN",
                        )
                    )
                    local_pos = existing.scalar_one_or_none()
                    if local_pos:
                        local_pos.current_price = pos.get("current_price")
                        local_pos.unrealized_pnl = pos.get("unrealized_pnl")
                        local_pos.liquidation_price = pos.get("liquidation_price")
                        local_pos.last_synced_at = dt.utcnow()
                    else:
                        session.add(CryptoPosition(
                            ticker=symbol,
                            side=pos.get("side", "Buy"),
                            size=pos.get("size", 0),
                            entry_price=pos.get("entry_price", 0),
                            current_price=pos.get("current_price"),
                            leverage=int(pos.get("leverage", 1)),
                            liquidation_price=pos.get("liquidation_price"),
                            unrealized_pnl=pos.get("unrealized_pnl"),
                            stop_loss=pos.get("stop_loss"),
                            take_profit=pos.get("take_profit"),
                        ))
                await session.commit()
            logger.info("crypto_position_sync_done", count=len(positions))
        except Exception as e:
            logger.error("crypto_position_sync_failed", error=str(e))

    # --- Phase 1: Lifecycle management job implementations ---

    async def _job_update_trailing_stops(self):
        """Adjust trailing stops for winning positions."""
        try:
            bybit = self.mcp._get_bybit()
            redis = self.redis_client
            from src.risk.trailing_stop import TrailingStopManager
            from src.models.tables import CryptoPosition
            from sqlalchemy import select

            manager = TrailingStopManager(bybit, redis)
            async with async_session() as session:
                result = await session.execute(
                    select(CryptoPosition).where(CryptoPosition.status == "OPEN")
                )
                # Snapshot columns inside session to avoid lazy-load errors
                positions = [
                    _snapshot_position(p) for p in result.scalars().all()
                ]

            if positions:
                # Exit Engine evaluates FIRST (Phase 4 — primary exit authority)
                blocked_by_exit_engine = set()
                if hasattr(self, 'exit_engine') and self.exit_engine:
                    for pos in positions:
                        try:
                            from src.architecture.position import Position, PositionState
                            arch_pos = Position(
                                symbol=pos.ticker, side=pos.side,
                                entry_price=float(pos.entry_price), quantity=float(pos.size),
                                leverage=pos.leverage or 1,
                                stop_loss=float(pos.stop_loss) if pos.stop_loss else None,
                                trailing_stop=float(pos.trailing_stop_price) if pos.trailing_stop_price else None,
                                state=PositionState.OPEN,
                            )
                            market_data = {
                                "mark_price": float(pos.current_price or 0),
                                "atr": 0, "kill_switch_active": False,
                            }
                            signal = self.exit_engine.evaluate(arch_pos, market_data)
                            if signal and signal.decision.value in ("EMERGENCY_EXIT", "STOP_LOSS", "FULL_EXIT"):
                                from src.metrics.crypto_metrics import record_exit_engine_block
                                record_exit_engine_block(signal.decision.value)
                                blocked_by_exit_engine.add(pos.ticker)
                                logger.critical("exit_engine_blocked_trailing",
                                               ticker=pos.ticker,
                                               decision=signal.decision.value,
                                               strategy=signal.strategy_name,
                                               reason=signal.reason)
                        except Exception as e:
                            logger.debug("exit_engine_error", ticker=pos.ticker, error=str(e))

                active_positions = [p for p in positions if p.ticker not in blocked_by_exit_engine]
                if active_positions:
                    actions = await manager.update_trailing_stops(active_positions)

                    # Profit Lock: tighten stops on winning positions
                    from src.risk.profit_lock import ProfitLockManager
                    pl = ProfitLockManager(bybit, self.redis_client)
                    lock_actions = await pl.check_profit_locks(active_positions)
                    if lock_actions:
                        logger.info("profit_locks_activated",
                                    count=len(lock_actions),
                                    tickers=[a["ticker"] for a in lock_actions])

                logger.info("trailing_stops_updated", total=len(positions), blocked=len(blocked_by_exit_engine))
        except Exception as e:
            logger.error("trailing_stop_job_failed", error=str(e))

    async def _job_check_partial_exits(self):
        """Check and execute partial exits at profit targets."""
        try:
            bybit = self.mcp._get_bybit()
            redis = self.redis_client
            from src.risk.position_manager import PositionManager
            from src.models.tables import CryptoPosition
            from sqlalchemy import select

            manager = PositionManager(bybit, redis)
            async with async_session() as session:
                result = await session.execute(
                    select(CryptoPosition).where(CryptoPosition.status == "OPEN")
                )
                positions = list(result.scalars().all())

            actions = await manager.check_partial_exits(positions)
            for action in actions:
                result = await manager.execute_partial_exit(
                    action["position_id"],
                    action["exit_pct"],
                    action["reason"],
                )
                if result.get("success"):
                    logger.info("partial_exit_executed", ticker=action["ticker"], exit_pct=action["exit_pct"])
        except Exception as e:
            logger.error("partial_exit_job_failed", error=str(e))

    async def _job_check_time_exits(self):
        """Close stale positions open >72h with <1% gain."""
        try:
            bybit = self.mcp._get_bybit()
            redis = self.redis_client
            from src.risk.position_manager import PositionManager
            from src.models.tables import CryptoPosition
            from sqlalchemy import select

            manager = PositionManager(bybit, redis)
            async with async_session() as session:
                result = await session.execute(
                    select(CryptoPosition).where(CryptoPosition.status == "OPEN")
                )
                positions = list(result.scalars().all())

            actions = await manager.check_time_exits(positions)
            for action in actions:
                from src.risk.sor import SmartOrderRouter
                sor = SmartOrderRouter(bybit)
                result = await sor.execute_order(
                    signal={"ticker": action["ticker"], "direction": "CLOSE", "confidence": 100},
                    risk_params={"qty": 0, "leverage": 1, "reduce_only": True},  # qty from position
                )
                if result.get("success"):
                    logger.info("time_exit_executed", ticker=action["ticker"], reason=action["reason"])
        except Exception as e:
            logger.error("time_exit_job_failed", error=str(e))

    async def _job_check_circuit_breakers(self):
        """Run circuit breaker checks (vol spike, correlation cascade)."""
        try:
            bybit = self.mcp._get_bybit()
            redis = self.redis_client
            from src.risk.circuit_breaker import CircuitBreakerManager

            manager = CircuitBreakerManager(redis, bybit)
            events = await manager.check_all()
            if events:
                logger.warning("circuit_breakers_triggered", events=len(events))
                # Send Telegram alert for HALT severity
                for event in events:
                    if event.get("severity") == "HALT":
                        try:
                            import httpx
                            token = settings.CRYPTO_TELEGRAM_TOKEN or settings.TELEGRAM_TOKEN
                            chat_id = settings.CRYPTO_TELEGRAM_CHAT_ID or settings.TELEGRAM_CHAT_ID
                            if token and chat_id:
                                msg = f"🚨 <b>CIRCUIT BREAKER: {event['breaker']}</b>\n\n{event}"
                                async with httpx.AsyncClient(timeout=10) as client:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                                    )
                        except Exception:
                            pass
        except Exception as e:
            logger.error("circuit_breaker_job_failed", error=str(e))

    async def _job_enforce_funding_limit(self):
        """Close positions with cumulative funding >3%."""
        try:
            bybit = self.mcp._get_bybit()
            from src.models.tables import CryptoPosition
            from sqlalchemy import select
            from src.config import settings

            funding_limit_pct = settings.CRYPTO_FUNDING_ALERT_THRESHOLD / 100  # reuse threshold

            async with async_session() as session:
                result = await session.execute(
                    select(CryptoPosition).where(CryptoPosition.status == "OPEN")
                )
                positions = list(result.scalars().all())

                for pos in positions:
                    if not pos.entry_price or pos.entry_price == 0:
                        continue
                    # Calculate cumulative funding as % of position value
                    position_value = float(pos.entry_price) * float(pos.size)
                    if position_value == 0:
                        continue
                    funding_pct = abs(float(pos.funding_cost_cumulative or 0)) / position_value
                    if funding_pct > 0.03:  # 3% cumulative funding threshold
                        from src.risk.sor import SmartOrderRouter
                        sor = SmartOrderRouter(bybit)
                        result = await sor.execute_order(
                            signal={"ticker": pos.ticker, "direction": "CLOSE", "confidence": 100},
                            risk_params={"qty": 0, "leverage": pos.leverage, "reduce_only": True},
                        )
                        if result.get("success"):
                            logger.warning("funding_limit_close", ticker=pos.ticker, funding_pct=round(funding_pct * 100, 2))
        except Exception as e:
            logger.error("funding_limit_job_failed", error=str(e))

    async def _job_reconcile_positions(self):
        """Bidirectional position reconciliation with Bybit."""
        try:
            bybit = self.mcp._get_bybit()
            from src.risk.position_sync import PositionReconciler

            reconciler = PositionReconciler(bybit)
            drifts = await reconciler.reconcile()
            if drifts:
                logger.warning("reconciliation_drifts", count=len(drifts))
                # Send Telegram alert for drifts
                try:
                    import httpx
                    token = settings.CRYPTO_TELEGRAM_TOKEN or settings.TELEGRAM_TOKEN
                    chat_id = settings.CRYPTO_TELEGRAM_CHAT_ID or settings.TELEGRAM_CHAT_ID
                    if token and chat_id:
                        drift_summary = "\n".join(f"• {d['drift_type']}: {d['ticker']}" for d in drifts)
                        msg = f"⚠️ <b>POSITION DRIFT DETECTED</b>\n\n{drift_summary}"
                        async with httpx.AsyncClient(timeout=10) as client:
                            await client.post(
                                f"https://api.telegram.org/bot{token}/sendMessage",
                                json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                            )
                except Exception:
                    pass
        except Exception as e:
            logger.error("reconciliation_job_failed", error=str(e))

    async def _job_flush_cache(self):
        """Flush cached OHLCV data from Redis to Postgres."""
        logger.info("cache_flush_started")
        # ponytail: iterate OHLCV keys in Redis, bulk upsert to ohlcv_cache table.
        logger.info("cache_flush_done")

    async def _job_liquidity_check(self):
        """Check orderbook liquidity for top pairs. Alerts on thin markets."""
        try:
            bybit = self.mcp._get_bybit()
            from src.risk.liquidity import LiquidityMonitor

            monitor = LiquidityMonitor(bybit)
            top_pairs = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
            alerts = []

            for ticker in top_pairs:
                liq = await monitor.check_liquidity(ticker, "BUY")
                if not liq["can_trade"]:
                    alerts.append(f"• {ticker}: {liq['reason']}")

            if alerts:
                # Check if alerts are muted
                alerts_on = await self.redis_client.get("karsa:alerts_enabled")
                if alerts_on in ("0", b"0"):
                    return
                try:
                    import httpx
                    token = settings.CRYPTO_TELEGRAM_TOKEN or settings.TELEGRAM_TOKEN
                    chat_id = settings.CRYPTO_TELEGRAM_CHAT_ID or settings.TELEGRAM_CHAT_ID
                    if token and chat_id:
                        msg = f"💧 <b>LIQUIDITY ALERT</b>\n\n" + "\n".join(alerts)
                        async with httpx.AsyncClient(timeout=10) as client:
                            await client.post(
                                f"https://api.telegram.org/bot{token}/sendMessage",
                                json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                            )
                except Exception:
                    pass

            logger.info("liquidity_check_done", alerts=len(alerts))
        except Exception as e:
            logger.error("liquidity_check_job_failed", error=str(e))

    async def _job_oms_cleanup(self):
        """Cancel stuck limit orders and sync OMS state with exchange."""
        try:
            if self.oms:
                stuck = await self.oms.cleanup_stuck_orders()
                await self.oms.sync_from_exchange()
                logger.info("oms_cleanup_done", stuck_cancelled=len(stuck))
        except Exception as e:
            logger.error("oms_cleanup_failed", error=str(e))

    async def _job_metrics_sync(self):
        """Polls health checks and balances to update Grafana metrics."""
        import time
        start = time.time()
        from src.metrics.crypto_metrics import (
            PORTFOLIO_EQUITY_USD, REDIS_CONNECTED, WARP_CONNECTED,
            OPEN_POSITIONS, UNREALIZED_PNL_USD
        )

        # ponytail: each section isolated — one failure shouldn't kill others

        # Redis health
        try:
            redis_ok = await self.cache.ping() if self.cache else False
            REDIS_CONNECTED.set(1 if redis_ok else 0)
        except Exception as e:
            REDIS_CONNECTED.set(0)
            logger.warning("metrics_redis_check_failed", error=str(e))

        # Bybit/WARP health + equity
        bybit = self.mcp._get_bybit()
        try:
            wallet = await bybit.get_wallet_balance()
            if not wallet.get("error"):
                WARP_CONNECTED.set(1)
                equity = wallet.get("equity", wallet.get("balance", 0))
                PORTFOLIO_EQUITY_USD.set(float(equity))
            else:
                WARP_CONNECTED.set(0)
        except Exception as e:
            WARP_CONNECTED.set(0)
            logger.warning("metrics_bybit_check_failed", error=str(e))

        # Positions
        try:
            positions = await bybit.get_positions()
            if not isinstance(positions, dict) or not positions.get("error"):
                OPEN_POSITIONS.set(len(positions))
                unrealized = sum(float(p.get("unrealized_pnl", 0)) for p in positions)
                UNREALIZED_PNL_USD.set(unrealized)
        except Exception as e:
            logger.warning("metrics_positions_check_failed", error=str(e))

        from src.metrics.crypto_metrics import JOB_LAST_RUN, JOB_DURATION
        JOB_LAST_RUN.labels(job_id="metrics_sync").set(time.time())
        JOB_DURATION.labels(job_id="metrics_sync").observe(time.time() - start)

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("shutting_down")
        from src.architecture.events import event_bus as _event_bus
        await _event_bus.stop()
        if hasattr(self, 'sl_engine') and self.sl_engine:
            await self.sl_engine.stop()
        if hasattr(self, 'ws_manager') and self.ws_manager:
            await self.ws_manager.stop()
        if self.scheduler and self.scheduler.running:
            # Finding 4: wait=True to let in-flight DB jobs complete
            self.scheduler.shutdown(wait=True)
        if self.mcp:
            await self.mcp.close()
        if self.redis_client:
            await self.redis_client.close()
        # Finding 6: close emergency module's separate Redis client
        from src.risk import emergency as _emergency
        await _emergency.close()
        await close_db()
        logger.info("shutdown_complete")

    async def run(self):
        """Main run loop — starts uvicorn + scheduler."""
        await self.startup()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: self._shutdown.set())

        logger.info("scheduler_running", jobs=len(self.scheduler.get_jobs()))

        # Run uvicorn on the main event loop to prevent cross-loop asyncpg
        # connection leaks (Finding 4 / P0).
        config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
        server = uvicorn.Server(config)
        loop.create_task(server.serve())
        logger.info("uvicorn_task_started", port=8000)

        # Listen for profile changes to auto-refresh universe
        if self.universe_engine:
            loop.create_task(self.universe_engine.listen_profile_changes())

        # Start WebSocket price streaming and stop-loss engine
        if self.ws_manager:
            loop.create_task(self.ws_manager.run())
        if self.sl_engine:
            loop.create_task(self.sl_engine.run())

        # Keep running until shutdown signal
        await self._shutdown.wait()
        # uvicorn is daemon thread — will die with the process
        await self.shutdown()

def main():
    setup_logging()
    karsa = KarsaApp()
    asyncio.run(karsa.run())

if __name__ == "__main__":
    main()
