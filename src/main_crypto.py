"""Karsa Trading System — Crypto-Only Orchestrator

Dedicated entry point for crypto trading. Runs only crypto-related
APScheduler jobs. No IDX/US/ETF scanning, no paper position updates,
no IDX intelligence.

Usage:
  python -m src.main_crypto

Shares the same Redis, Postgres, and 9router as the main orchestrator.
Health endpoint on port 8001 (main orchestrator uses 8000).
"""

import asyncio
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

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
from src.metrics.crypto_metrics import JOB_LAST_RUN, JOB_DURATION, JOB_ERRORS, DAILY_LOSS_PCT

logger = get_logger("main_crypto")


from src.utils.position_snapshot import snapshot_from_db as _snapshot_position


app = FastAPI(title="Karsa Crypto Orchestrator", version="0.1.0")
karsa_app: "CryptoKarsaApp | None" = None


class CryptoKarsaApp:
    """Crypto-only application container."""

    def __init__(self):
        self.redis_client: redis.Redis | None = None
        self.cache: CacheManager | None = None
        self.mcp: MCPClient | None = None
        self.rate_limiter: RateLimiter | None = None
        self.orchestrator: Orchestrator | None = None
        self.scheduler: AsyncIOScheduler | None = None
        self._shutdown = asyncio.Event()

    async def startup(self):
        """Initialize services and register crypto-only jobs."""
        logger.info("starting_karsa_crypto", version="0.1.0")

        await init_db()
        logger.info("database_ready")

        self.redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.cache = CacheManager(self.redis_client)

        if not await self.cache.ping():

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

        # Position Manager shadow (Phase 3 — single writer, observe mode)
        from src.architecture.position import PositionManager, UpdateTrailingStop
        self.arch_position_manager = PositionManager(event_bus=_event_bus)
        logger.info("arch_position_manager_ready")

        # Decision Engine (Phase 5 — single decision authority)
        from src.architecture.decision import DecisionEngine, AnalyzerSource, PolicySource
        self.decision_engine = DecisionEngine()
        self.decision_engine.add_source(AnalyzerSource())
        self.decision_engine.add_source(PolicySource())
        logger.info("decision_engine_ready", sources=[s.name for s in self.decision_engine._sources])

        # Replay Engine (Phase 6 — event store subscriber)
        from src.architecture.replay import ReplayEngine
        self.replay_engine = ReplayEngine()
        _replay_subscriber = self.replay_engine.store_event
        for evt_type in ["PositionOpened", "PositionReduced", "PositionClosed",
                         "TrailingActivated", "BreakEvenActivated", "StopLossTriggered",
                         "StopLossRecovered", "StopLossUpdated", "PositionSynced"]:
            _event_bus.subscribe(evt_type, _replay_subscriber)
        logger.info("replay_engine_ready")

        # Policy Engine (Phase 7 — single policy authority)
        from src.architecture.policy import PolicyEngine, TradingPolicy, RiskPolicy, EmergencyPolicy
        self.policy_engine = PolicyEngine()
        self.policy_engine.add_policy(EmergencyPolicy.kill_switch())
        self.policy_engine.add_policy(EmergencyPolicy.circuit_breaker())
        self.policy_engine.add_policy(RiskPolicy.daily_loss_limit())
        self.policy_engine.add_policy(RiskPolicy.max_leverage())
        self.policy_engine.add_policy(TradingPolicy.trading_mode_check())
        self.policy_engine.add_policy(TradingPolicy.max_positions_check())
        logger.info("policy_engine_ready", policies=[p["name"] for p in self.policy_engine.list_policies()])

        # Agent Runtime (Phase 8 — lifecycle management)
        from src.architecture.agent_runtime import AgentRuntime, AgentRegistry, AgentConfig
        self.agent_runtime = AgentRuntime(max_concurrent=5)
        self.agent_registry = AgentRegistry()
        self.agent_registry.register(AgentConfig("crypto_analyst", max_retries=3, timeout_seconds=120, combo_name="karsa-routine"))
        self.agent_registry.register(AgentConfig("crypto_auditor", max_retries=2, timeout_seconds=60, combo_name="karsa-routine"))
        logger.info("agent_runtime_ready", agents=self.agent_registry.all_types())

        # Workflow Engine (Phase 9 — durable business processes)
        from src.architecture.workflow import WorkflowEngine, CheckpointManager
        self.checkpoint_manager = CheckpointManager(redis_client=self.redis_client)
        self.workflow_engine = WorkflowEngine(checkpoint_manager=self.checkpoint_manager)
        logger.info("workflow_engine_ready")

        # Ponytail: move MCPClient + Orchestrator init before attribute attachment
        self.mcp = MCPClient(self.cache)
        self.orchestrator = Orchestrator(self.mcp, self.cache, self.rate_limiter)
        self.orchestrator.decision_engine = self.decision_engine
        self.orchestrator.policy_engine = self.policy_engine
        self.orchestrator.agent_runtime = self.agent_runtime
        self.orchestrator.workflow_engine = self.workflow_engine

        # Risk profile + dynamic universe engine
        from src.risk.profile_manager import RiskProfileManager
        from src.advisory.crypto_universe import UniverseEngine
        self.profile_manager = RiskProfileManager(self.redis_client)
        await self.profile_manager.ensure_default()
        self.orchestrator.profile_manager = self.profile_manager
        bybit = self.mcp._get_bybit()
        self.universe_engine = UniverseEngine(bybit, self.redis_client, self.profile_manager)
        self.orchestrator.universe_engine = self.universe_engine

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

        # Autonomous Session resurrection — check for interrupted session
        try:
            is_active = await self.redis_client.get("karsa:auto:state:active")
            if is_active in ("1", b"1"):
                logger.critical("resurrecting_autonomous_session")
                from src.agents.autonomous_session import AutonomousSessionManager
                asm = AutonomousSessionManager(self.orchestrator, self.redis_client, bybit)
                # Clear any leftover emergency/halt from the previous session
                await asm._clear_stale_emergency()
                chat_id = int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else 0
                asyncio.create_task(asm._run_loop(chat_id))
        except Exception as e:
            logger.warning("asm_resurrection_failed", error=str(e))

        logger.info("orchestrator_ready")

        jobstores = {"default": MemoryJobStore()}
        self.scheduler = AsyncIOScheduler(jobstores=jobstores)
        self._register_jobs()
        self.scheduler.start()
        logger.info("scheduler_started")

        # Seed initial PnL snapshot so Grafana has data immediately
        try:
            await self._job_crypto_pnl_snapshot()
            logger.info("startup_pnl_snapshot_done")
        except Exception as e:
            logger.error("startup_pnl_snapshot_failed", error=str(e))

        # Schedule an immediate scan run on startup (run 5 seconds from now)
        self.scheduler.add_job(
            self._job_scan_crypto,
            trigger="date",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=5),
            id="scan_crypto_startup",
            name="Crypto Market Scan (Startup)"
        )

        self._register_health_routes()
        global karsa_app
        karsa_app = self
        logger.info("karsa_crypto_ready")

    def _register_health_routes(self):
        @app.get("/health")
        async def health():
            db_ok = False
            pool_healthy = True
            try:
                from sqlalchemy import text
                from src.models.database import get_engine
                engine = get_engine()
                pool = engine.pool

                # Check for negative overflow (connection leak)
                if pool.overflow() < 0:
                    logger.warning("health_check_pool_leak overflow=%d — forcing dispose", pool.overflow())
                    await engine.dispose()
                    import src.models.database as db_module
                    db_module._engine = None
                    db_module._session_factory = None
                    pool_healthy = False

                async with async_session() as session:
                    await session.execute(text("SELECT 1"))
                    db_ok = True
            except Exception:
                pass
            redis_ok = await self.cache.ping() if self.cache else False
            return {
                "status": "ok" if (db_ok and redis_ok) else "degraded",
                "mode": "crypto_only",
                "trading_mode": settings.TRADING_MODE,
                "checks": {
                    "postgres": "ok" if db_ok else "FAIL",
                    "redis": "ok" if redis_ok else "FAIL",
                    "pool": "ok" if pool_healthy else "reset",
                },
            }

        @app.get("/health/scheduler")
        async def scheduler_status():
            if not self.scheduler:
                return {"status": "not_initialized", "jobs": []}
            jobs = [{"id": j.id, "name": j.name, "next_run": j.next_run_time.isoformat() if j.next_run_time else None} for j in self.scheduler.get_jobs()]
            return {"status": "running" if self.scheduler.running else "stopped", "jobs": jobs, "job_count": len(jobs)}

        @app.get("/metrics")
        async def metrics():
            import src.metrics.crypto_metrics  # ensure registered
            from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
            from fastapi.responses import Response
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    def _register_jobs(self):
        """Register only crypto-related APScheduler jobs."""
        s = self.scheduler

        if not settings.BYBIT_API_KEY:
            logger.warning("bybit_api_key_missing_no_crypto_jobs")
            return

        # Core crypto (24/7)
        s.add_job(self._job_scan_crypto, "cron", minute="*/15",
                  id="scan_crypto", name="Crypto Market Scan (24/7)", replace_existing=True, misfire_grace_time=600, max_instances=1, coalesce=True)
        s.add_job(self._job_refresh_universe, "cron", minute="*/15",
                  id="refresh_universe", name="Crypto Universe Refresh (every 15m)", replace_existing=True, misfire_grace_time=600, max_instances=1, coalesce=True)
        s.add_job(self._job_monitor_crypto_positions, "cron", minute="*/15",
                  id="crypto_monitor", name="Crypto Position Monitor", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)
        s.add_job(self._job_sync_crypto_funding, "cron", hour="0,8,16", minute=5,
                  id="crypto_funding", name="Crypto Funding Rate Sync", replace_existing=True, misfire_grace_time=300, max_instances=1, coalesce=True)
        s.add_job(self._job_crypto_pnl_snapshot, "cron", hour=0, minute=0,
                  id="crypto_pnl_snapshot", name="Crypto Daily PnL Snapshot", replace_existing=True, misfire_grace_time=600, max_instances=1, coalesce=True)
        s.add_job(self._job_sync_crypto_positions, "cron", minute="*/5",
                  id="crypto_position_sync", name="Crypto Position Sync", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)

        # Lifecycle management — DB-heavy jobs get max_instances=1 to prevent
        # connection pool exhaustion from concurrent runs.
        s.add_job(self._job_update_trailing_stops, "cron", minute="*/5",
                  id="crypto_trailing_stops", name="Crypto Trailing Stop Update", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)
        s.add_job(self._job_verify_sl, "interval", minutes=5,
                  id="sl_verification", name="SL Verification & Recovery", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)
        s.add_job(self._job_check_partial_exits, "cron", minute="*/2",
                  id="crypto_partial_exits", name="Crypto Partial Exit Check", replace_existing=True, misfire_grace_time=60, max_instances=1, coalesce=True)
        s.add_job(self._job_check_time_exits, "cron", hour="*", minute=30,
                  id="crypto_time_exits", name="Crypto Time-Based Exit Check", replace_existing=True, misfire_grace_time=300, max_instances=1, coalesce=True)
        s.add_job(self._job_check_performance_gate, "cron", minute="*/5",
                  id="crypto_perf_gate", name="Performance Gate + AI Judge", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)
        s.add_job(self._job_check_circuit_breakers, "cron", minute="*/1",
                  id="crypto_circuit_breakers", name="Crypto Circuit Breaker Check", replace_existing=True, misfire_grace_time=60, max_instances=1, coalesce=True)
        s.add_job(self._job_enforce_funding_limit, "cron", hour="*", minute=20,
                  id="crypto_funding_limit", name="Crypto Funding Limit Enforcement", replace_existing=True, misfire_grace_time=300, max_instances=1, coalesce=True)
        s.add_job(self._job_reconcile_positions, "interval", seconds=60,
                  id="crypto_reconciliation", name="Crypto Position Reconciliation", replace_existing=True, misfire_grace_time=30, max_instances=1, coalesce=True)
        s.add_job(self._job_liquidity_check, "cron", minute="*/15",
                  id="crypto_liquidity", name="Crypto Liquidity Check", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)
        s.add_job(self._job_oms_cleanup, "interval", minutes=2,
                  id="oms_cleanup", name="OMS Stuck Order Cleanup", replace_existing=True, misfire_grace_time=60, max_instances=1, coalesce=True)
        s.add_job(self._job_kill_switch, "cron", minute="*/5",
                  id="kill_switch", name="Crypto Kill Switch", replace_existing=True, misfire_grace_time=60, max_instances=1, coalesce=True)
        s.add_job(self._job_metrics_sync, "interval", seconds=60,
                  id="crypto_metrics_sync", name="Crypto Metrics Sync", replace_existing=True, misfire_grace_time=30, max_instances=1, coalesce=True)

        # Funding capture strategy (aligned with funding settlement epochs)
        s.add_job(self._job_scan_funding, "cron", hour="1,5,9,13,17,21", minute=10,
                  id="funding_capture", name="Funding Capture Scan", replace_existing=True, misfire_grace_time=300, max_instances=1, coalesce=True)

        # AODE Research Jobs (only scheduled when AODE feature is enabled)
        if settings.AODE_ENABLED:
            s.add_job(self._job_aode_discovery, "cron", hour="*/1",
                      id="aode_discovery", name="AODE Token Discovery", replace_existing=True, misfire_grace_time=300, max_instances=1, coalesce=True)
            s.add_job(self._job_aode_research, "cron", hour="*/4",
                      id="aode_research", name="AODE Research Scoring", replace_existing=True, misfire_grace_time=600, max_instances=1, coalesce=True)
            s.add_job(self._job_aode_monitoring, "cron", minute="*/30",
                      id="aode_monitoring", name="AODE Monitoring Cycle", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)

        logger.info("crypto_jobs_registered", count=len(self.scheduler.get_jobs()))

    # --- Job implementations ---

    async def _job_scan_crypto(self):
        start = time.time()
        try:
            signals = await self.orchestrator.scan_all_markets("CRYPTO")
            JOB_LAST_RUN.labels(job_id="scan_crypto").set(time.time())
            JOB_DURATION.labels(job_id="scan_crypto").observe(time.time() - start)
            logger.info("crypto_scan_done", signals=len(signals))
        except Exception as e:
            JOB_ERRORS.labels(job_id="scan_crypto").inc()
            logger.error("crypto_scan_failed", error=str(e))

    async def _job_metrics_sync(self):
        """Polls health checks and balances to update Grafana metrics."""
        start = time.time()
        from src.metrics.crypto_metrics import (
            PORTFOLIO_EQUITY_USD, REDIS_CONNECTED, WARP_CONNECTED,
            OPEN_POSITIONS, UNREALIZED_PNL_USD,
            WALLET_TOTAL_EQUITY, WALLET_AVAILABLE, WALLET_USED_MARGIN
        )
        # ponytail: each metric section isolated — one failure shouldn't kill others

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
                equity = float(wallet.get("equity", wallet.get("balance", 0)))
                available = float(wallet.get("available", 0))
                used_margin = equity - available
                PORTFOLIO_EQUITY_USD.set(equity)
                WALLET_TOTAL_EQUITY.set(equity)
                WALLET_AVAILABLE.set(available)
                WALLET_USED_MARGIN.set(max(0, used_margin))
            else:
                WARP_CONNECTED.set(0)
        except Exception as e:
            WARP_CONNECTED.set(0)
            logger.warning("metrics_bybit_check_failed", error=str(e))

        # Positions
        try:
            positions = await bybit.get_positions()
            if not isinstance(positions, dict) or not positions.get("error"):
                from src.metrics.crypto_metrics import (
                    POSITION_PNL, POSITION_ENTRY_PRICE, POSITION_MARK_PRICE, POSITION_SIZE
                )
                # Count only non-zero size positions
                active = [p for p in positions if float(p.get("size", 0)) > 0]
                OPEN_POSITIONS.set(len(active))
                unrealized = sum(float(p.get("unrealisedPnl", 0)) for p in active)
                UNREALIZED_PNL_USD.set(unrealized)
                # Update per-position metrics
                for p in active:
                    t = p.get("symbol", "")
                    s = p.get("side", "")
                    POSITION_PNL.labels(ticker=t, side=s).set(float(p.get("unrealisedPnl", 0)))
                    POSITION_ENTRY_PRICE.labels(ticker=t, side=s).set(float(p.get("avgPrice", 0)))
                    POSITION_MARK_PRICE.labels(ticker=t, side=s).set(float(p.get("markPrice", 0)))
                    POSITION_SIZE.labels(ticker=t, side=s).set(float(p.get("size", 0)))
        except Exception as e:
            logger.warning("metrics_positions_check_failed", error=str(e))

        JOB_LAST_RUN.labels(job_id="metrics_sync").set(time.time())
        JOB_DURATION.labels(job_id="metrics_sync").observe(time.time() - start)

    async def _job_refresh_universe(self):
        start = time.time()
        try:
            if self.universe_engine:
                universe = await self.universe_engine.generate()
                JOB_LAST_RUN.labels(job_id="refresh_universe").set(time.time())
                JOB_DURATION.labels(job_id="refresh_universe").observe(time.time() - start)
                logger.info("universe_refreshed", count=len(universe))
        except Exception as e:
            JOB_ERRORS.labels(job_id="refresh_universe").inc()
            logger.error("universe_refresh_failed", error=str(e))

    async def _job_monitor_crypto_positions(self):
        start = time.time()
        try:
            bybit = self.mcp._get_bybit()
            positions = await bybit.get_positions()
            if positions:
                for pos in positions:
                    logger.info("crypto_position", ticker=pos.get("symbol"), pnl=pos.get("unrealisedPnl"), size=pos.get("size"))
            JOB_LAST_RUN.labels(job_id="crypto_monitor").set(time.time())
            JOB_DURATION.labels(job_id="crypto_monitor").observe(time.time() - start)
        except Exception as e:
            JOB_ERRORS.labels(job_id="crypto_monitor").inc()
            logger.error("crypto_monitor_failed", error=str(e))

    async def _job_sync_crypto_funding(self):
        start = time.time()
        try:
            from src.risk.funding_tracker import FundingTracker
            bybit = self.mcp._get_bybit()
            tracker = FundingTracker(bybit)
            rates = await tracker.get_current_rates()
            alerts = [r for r in rates if r.get("alert")]
            if alerts:
                logger.warning("funding_alerts", alerts=alerts)
            JOB_LAST_RUN.labels(job_id="crypto_funding").set(time.time())
            JOB_DURATION.labels(job_id="crypto_funding").observe(time.time() - start)
            logger.info("crypto_funding_synced", rates=len(rates))
        except Exception as e:
            JOB_ERRORS.labels(job_id="crypto_funding").inc()
            logger.error("crypto_funding_sync_failed", error=str(e))

    async def _job_crypto_pnl_snapshot(self):
        start = time.time()
        try:
            from src.models.tables import CryptoPnLSnapshot, ClosedPaperTrade
            from src.models.database import async_session
            from datetime import datetime as dt
            from sqlalchemy import select, func, cast, Date

            bybit = self.mcp._get_bybit()
            wallet = await bybit.get_wallet_balance()
            positions = await bybit.get_positions()
            unrealized = sum(p.get("unrealized_pnl", 0) for p in positions)

            async with async_session() as session:
                today = dt.utcnow().date()
                realized_result = await session.execute(
                    select(func.sum(ClosedPaperTrade.realized_pnl)).where(
                        ClosedPaperTrade.market == "CRYPTO",
                        ClosedPaperTrade.exit_date >= dt.combine(today, dt.min.time()),
                    )
                )
                realized = realized_result.scalar() or 0

                funding_result = await session.execute(
                    select(func.sum(CryptoPnLSnapshot.funding_costs)).where(
                        CryptoPnLSnapshot.snapshot_date >= dt.combine(today, dt.min.time())
                    )
                )
                funding = funding_result.scalar() or 0

                session.add(CryptoPnLSnapshot(
                    snapshot_date=dt.utcnow(),
                    realized_pnl=float(realized),
                    unrealized_pnl=unrealized,
                    funding_costs=float(funding),
                    total_pnl=float(realized) + unrealized - float(funding),
                    equity=wallet.get("total_equity", wallet.get("balance", 0)),
                    open_positions=len(positions),
                ))
                await session.commit()

            JOB_LAST_RUN.labels(job_id="crypto_pnl_snapshot").set(time.time())
            JOB_DURATION.labels(job_id="crypto_pnl_snapshot").observe(time.time() - start)
            logger.info("crypto_pnl_snapshot_done", equity=wallet.get("total_equity", 0))
        except Exception as e:
            JOB_ERRORS.labels(job_id="crypto_pnl_snapshot").inc()
            logger.error("crypto_pnl_snapshot_failed", error=str(e))

    async def _job_sync_crypto_positions(self):
        start = time.time()
        try:
            bybit = self.mcp._get_bybit()
            positions = await bybit.get_positions()
            JOB_LAST_RUN.labels(job_id="crypto_position_sync").set(time.time())
            JOB_DURATION.labels(job_id="crypto_position_sync").observe(time.time() - start)
            logger.info("crypto_position_sync_done", count=len(positions) if positions else 0)
        except Exception as e:
            JOB_ERRORS.labels(job_id="crypto_position_sync").inc()
            logger.error("crypto_position_sync_failed", error=str(e))

    async def _job_update_trailing_stops(self):
        start = time.time()
        try:
            from src.risk.trailing_stop import TrailingStopManager
            from src.models.tables import CryptoPosition
            from src.models.database import async_session
            from sqlalchemy import select
            bybit = self.mcp._get_bybit()
            manager = TrailingStopManager(bybit, self.redis_client)
            positions_data = await bybit.get_positions()
            if not positions_data:
                JOB_LAST_RUN.labels(job_id="crypto_trailing_stops").set(time.time())
                return
            # Query DB by ticker to get real IDs (required for DB updates).
            # Extract all needed attributes INSIDE the session to avoid
            # lazy-load / event-loop-mismatch errors after session closes.
            positions = []
            tickers = [p.get("symbol", "") for p in positions_data if float(p.get("size", 0) or 0) > 0]
            async with async_session() as session:
                for ticker in tickers:
                    result = await session.execute(
                        select(CryptoPosition).where(
                            CryptoPosition.ticker == ticker,
                            CryptoPosition.status == "OPEN",
                        ).order_by(CryptoPosition.id.desc()).limit(1)
                    )
                    db_pos = result.scalar_one_or_none()
                    if db_pos:
                        # Snapshot all columns to detach from session
                        positions.append(_snapshot_position(db_pos))
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
                            if signal and signal.decision.value != "CONTINUE":
                                logger.warning("exit_engine_decision",
                                              ticker=pos.ticker,
                                              decision=signal.decision.value,
                                              strategy=signal.strategy_name,
                                              reason=signal.reason,
                                              priority=signal.priority)
                                # Block trailing stop for emergency, stop-loss, and full exit
                                if signal.decision.value in ("EMERGENCY_EXIT", "STOP_LOSS", "FULL_EXIT"):
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

                # Filter out positions blocked by ExitEngine
                active_positions = [p for p in positions if p.ticker not in blocked_by_exit_engine]

                if active_positions:
                    await manager.update_trailing_stops(active_positions)

                    from src.risk.profit_lock import ProfitLockManager
                    pl = ProfitLockManager(bybit, self.redis_client)
                    lock_actions = await pl.check_profit_locks(active_positions)
                    if lock_actions:
                        logger.info("profit_locks_activated",
                                    count=len(lock_actions),
                                    tickers=[a["ticker"] for a in lock_actions])

            JOB_LAST_RUN.labels(job_id="crypto_trailing_stops").set(time.time())
            JOB_DURATION.labels(job_id="crypto_trailing_stops").observe(time.time() - start)
            logger.info("trailing_stops_updated", count=len(positions))

            # Position Manager shadow tracking (Phase 3 — observe only)
            if hasattr(self, 'arch_position_manager') and self.arch_position_manager and positions:
                try:
                    for pos in positions:
                        cmd = UpdateTrailingStop(
                            position_id=f"db:{pos.id}",
                            new_trail_stop=float(pos.trailing_stop_price) if pos.trailing_stop_price else 0,
                            highest_price=float(pos.current_price) if pos.current_price else None,
                        )
                        if cmd.new_trail_stop > 0:
                            await self.arch_position_manager.update_trailing_stop(cmd)
                except Exception as e:
                    logger.debug("position_manager_shadow_error", error=str(e))
        except Exception as e:
            JOB_ERRORS.labels(job_id="crypto_trailing_stops").inc()
            logger.error("trailing_stop_job_failed", error=str(e))

    async def _job_verify_sl(self):
        """Verify all positions have active SL orders. Recover missing ones."""
        start = time.time()
        try:
            from src.risk.position_manager import PositionManager
            from src.models.tables import CryptoPosition
            from src.models.database import async_session
            from sqlalchemy import select
            bybit = self.mcp._get_bybit()
            pm = PositionManager(bybit, self.redis_client)
            positions = await bybit.get_positions()
            if not positions:
                JOB_LAST_RUN.labels(job_id="sl_verification").set(time.time())
                return
            # Query DB by ticker to get real IDs (required for DB updates).
            # Snapshot columns inside session to avoid lazy-load errors.
            crypto_positions = []
            tickers = [p.get("symbol", "") for p in positions if float(p.get("size", 0) or 0) > 0]
            async with async_session() as session:
                for ticker in tickers:
                    result = await session.execute(
                        select(CryptoPosition).where(
                            CryptoPosition.ticker == ticker,
                            CryptoPosition.status == "OPEN",
                        ).order_by(CryptoPosition.id.desc()).limit(1)
                    )
                    db_pos = result.scalar_one_or_none()
                    if db_pos:
                        crypto_positions.append(_snapshot_position(db_pos))
            if crypto_positions:
                recoveries = await pm.verify_and_recover_sl(crypto_positions)
                if recoveries:
                    logger.warning("sl_recoveries", count=len(recoveries),
                                   tickers=[r["ticker"] for r in recoveries])
            JOB_LAST_RUN.labels(job_id="sl_verification").set(time.time())
            JOB_DURATION.labels(job_id="sl_verification").observe(time.time() - start)
        except Exception as e:
            JOB_ERRORS.labels(job_id="sl_verification").inc()
            logger.error("sl_verification_job_failed", error=str(e))

    async def _job_check_partial_exits(self):
        start = time.time()
        try:
            from src.risk.position_manager import PositionManager
            from src.models.tables import CryptoPosition
            from src.models.database import async_session
            from sqlalchemy import select

            bybit = self.mcp._get_bybit()
            pm = PositionManager(bybit, self.redis_client)

            # Fetch from DB to get actual position IDs and current state
            async with async_session() as session:
                result = await session.execute(
                    select(CryptoPosition).where(CryptoPosition.status == "OPEN")
                )
                crypto_positions = list(result.scalars().all())

            if not crypto_positions:
                JOB_LAST_RUN.labels(job_id="crypto_partial_exits").set(time.time())
                return

            actions = await pm.check_partial_exits(crypto_positions)
            for action in actions:
                await pm.execute_partial_exit(
                    position_id=action["position_id"],
                    exit_pct=action["exit_pct"],
                    reason=action["reason"],
                )
            JOB_LAST_RUN.labels(job_id="crypto_partial_exits").set(time.time())
            JOB_DURATION.labels(job_id="crypto_partial_exits").observe(time.time() - start)
            if actions:
                logger.info("partial_exits_executed", count=len(actions))
        except Exception as e:
            JOB_ERRORS.labels(job_id="crypto_partial_exits").inc()
            logger.error("partial_exit_job_failed", error=str(e))

    async def _job_check_time_exits(self):
        start = time.time()
        try:
            from src.risk.position_manager import PositionManager
            from src.models.tables import CryptoPosition
            from src.models.database import async_session
            from sqlalchemy import select

            bybit = self.mcp._get_bybit()
            pm = PositionManager(bybit, self.redis_client)

            # Fetch from DB to get actual position IDs with opened_at
            async with async_session() as session:
                result = await session.execute(
                    select(CryptoPosition).where(CryptoPosition.status == "OPEN")
                )
                crypto_positions = list(result.scalars().all())

            if not crypto_positions:
                JOB_LAST_RUN.labels(job_id="crypto_time_exits").set(time.time())
                return

            actions = await pm.check_time_exits(crypto_positions)
            for action in actions:
                # Time exits are full closes — use exit_pct=100
                await pm.execute_partial_exit(
                    position_id=action["position_id"],
                    exit_pct=100,
                    reason=action["reason"],
                )
            JOB_LAST_RUN.labels(job_id="crypto_time_exits").set(time.time())
            JOB_DURATION.labels(job_id="crypto_time_exits").observe(time.time() - start)
            if actions:
                logger.info("time_exits_executed", count=len(actions))
        except Exception as e:
            JOB_ERRORS.labels(job_id="crypto_time_exits").inc()
            logger.error("time_exit_job_failed", error=str(e))

    async def _job_check_performance_gate(self):
        """Performance Gate: mechanical checkpoints + AI judge for ambiguous positions.

        Layer 1 (mechanical): hard fail → instant exit, clear win → hold.
        Layer 2 (AI judge): ambiguous → cheap LLM pass → still bad → escalated pass → exit.
        Runs every 5 min. Replaces the old single-threshold time-exit logic.
        """
        start = time.time()
        try:
            from src.risk.performance_gate import PerformanceGate, GateAction
            from src.agents.position_judge import PositionJudge
            from src.risk.sor import SmartOrderRouter
            from src.models.tables import CryptoPosition
            from src.models.database import async_session
            from sqlalchemy import select

            bybit = self.mcp._get_bybit()
            gate = PerformanceGate(self.redis_client)
            judge = PositionJudge(self.mcp, self.rate_limiter)
            sor = SmartOrderRouter(bybit, oms=self.oms)

            # Get open positions from DB (need full ORM objects with opened_at, signal_source, etc.)
            async with async_session() as session:
                result = await session.execute(
                    select(CryptoPosition).where(CryptoPosition.status == "OPEN")
                )
                positions = list(result.scalars().all())

            if not positions:
                JOB_LAST_RUN.labels(job_id="perf_gate").set(time.time())
                return

            # Layer 1: mechanical checkpoint evaluation
            gate_results = await gate.evaluate_all(positions)

            for gr in gate_results:
                try:
                    if gr.action == GateAction.EXIT:
                        # Hard fail — instant exit, no LLM
                        await self._execute_gate_exit(sor, bybit, gr.ticker, gr.reason, gr.gain_pct, gr.position_id)
                        logger.warning("gate_exit", ticker=gr.ticker, reason=gr.reason, gain_pct=gr.gain_pct)

                    elif gr.action == GateAction.JUDGE:
                        # Ambiguous — fire AI judge
                        pos = next((p for p in positions if p.id == gr.position_id), None)
                        if not pos:
                            continue

                        position_data = {
                            "ticker": gr.ticker,
                            "side": pos.side,
                            "entry_price": float(pos.entry_price),
                            "current_price": float(pos.current_price or 0),
                            "gain_pct": gr.gain_pct,
                            "hours_held": gr.hours_held,
                            "bucket": gr.bucket,
                            "gate_reason": gr.reason,
                            "consecutive_holds": await gate._get_consecutive_holds(gr.position_id),
                        }

                        if gr.escalation:
                            # Escalated pass: prior judge said HOLD, still bad
                            prior = await gate._get_prior_judgment(gr.position_id)
                            position_data["prior_judgment"] = prior
                            judgment = await judge.escalated_pass(position_data)
                        else:
                            # Cheap pass: first AI evaluation
                            judgment = await judge.cheap_pass(position_data)

                        # Record judgment for escalation tracking
                        await gate.record_judgment(gr.position_id, judgment)

                        if judgment["action"] == "EXIT":
                            await self._execute_gate_exit(
                                sor, bybit, gr.ticker,
                                f"AI judge ({'escalated' if gr.escalation else 'cheap'}): {judgment['reason']}",
                                gr.gain_pct, gr.position_id,
                            )
                            logger.warning("judge_exit", ticker=gr.ticker, judgment=judgment, gain_pct=gr.gain_pct)
                        elif judgment["action"] == "TIGHTEN_STOP":
                            # Tighten stop loss — save to DB and exchange
                            new_stop_pct = judgment.get("new_stop_pct")
                            if new_stop_pct and pos.current_price:
                                from decimal import Decimal
                                if pos.side == "Buy":
                                    new_sl = float(pos.current_price) * (1 + new_stop_pct / 100)
                                else:
                                    new_sl = float(pos.current_price) * (1 - new_stop_pct / 100)
                                await bybit.set_stop_loss(gr.ticker, new_sl, pos.side)
                                # Save dynamic_stop_pct to DB
                                async with async_session() as db_session:
                                    db_pos = await db_session.get(CryptoPosition, gr.position_id)
                                    if db_pos:
                                        db_pos.dynamic_stop_pct = Decimal(str(new_stop_pct))
                                        await db_session.commit()
                                logger.info("judge_tighten_stop", ticker=gr.ticker, new_sl=new_sl, new_stop_pct=new_stop_pct, reason=judgment["reason"])
                        else:
                            logger.info("judge_hold", ticker=gr.ticker, confidence=judgment["confidence"], reason=judgment["reason"])

                    elif gr.action == GateAction.HOLD:
                        # Clear win — save dynamic_stop_pct to DB if gate set one
                        if gr.zone == "clear_win" and gr.reason and "dynamic stop set to" in gr.reason:
                            pos = next((p for p in positions if p.id == gr.position_id), None)
                            if pos:
                                # Extract stop % from reason string
                                import re
                                from decimal import Decimal as D
                                match = re.search(r"dynamic stop set to ([\d.]+)%", gr.reason)
                                if match:
                                    stop_pct = float(match.group(1))
                                    async with async_session() as db_session:
                                        db_pos = await db_session.get(CryptoPosition, gr.position_id)
                                        if db_pos:
                                            db_pos.dynamic_stop_pct = D(str(stop_pct))
                                            await db_session.commit()
                                    logger.info("clear_win_dynamic_stop", ticker=gr.ticker, dynamic_stop_pct=stop_pct)

                except Exception as e:
                    logger.error("gate_result_failed", ticker=gr.ticker, error=str(e))

            # Record performance gate metrics
            try:
                from src.metrics.crypto_metrics import (
                    record_perf_gate_zone, record_perf_gate_exit,
                    update_dynamic_stop_active, update_consecutive_holds,
                )
                for gr in gate_results:
                    record_perf_gate_zone(gr.zone, gr.bucket)
                    if gr.action == GateAction.EXIT:
                        reason_type = "dynamic_stop" if "dynamic stop" in gr.reason else \
                                      "hard_fail" if "hard fail" in gr.reason else \
                                      "consecutive_holds" if "consecutive holds" in gr.reason else \
                                      "judge_exit"
                        record_perf_gate_exit(reason_type)
                for pos in positions:
                    has_dynamic = getattr(pos, 'dynamic_stop_pct', None) is not None
                    update_dynamic_stop_active(pos.ticker, has_dynamic)
            except Exception:
                pass

            JOB_LAST_RUN.labels(job_id="perf_gate").set(time.time())
            JOB_DURATION.labels(job_id="perf_gate").observe(time.time() - start)
            if gate_results:
                logger.info("perf_gate_done", evaluated=len(positions), actions=len(gate_results))

        except Exception as e:
            JOB_ERRORS.labels(job_id="perf_gate").inc()
            logger.error("perf_gate_job_failed", error=str(e))

    async def _execute_gate_exit(self, sor, bybit, ticker: str, reason: str, gain_pct: float, position_id: int):
        """Execute a performance gate exit: close position via SOR, update DB."""
        from src.models.tables import CryptoPosition, ClosedPaperTrade
        from src.models.database import async_session as _async_session
        from sqlalchemy import select
        from datetime import datetime, timezone
        from decimal import Decimal

        # Get current position from Bybit
        positions = await bybit.get_positions()
        pos_data = next((p for p in positions if p.get("symbol") == ticker and float(p.get("size", 0) or 0) > 0), None)

        if not pos_data:
            logger.warning("gate_exit_no_position", ticker=ticker)
            return

        size = float(pos_data.get("size", 0))
        side = pos_data.get("side", "Buy")
        close_side = "Sell" if side == "Buy" else "Buy"

        # Close via SOR
        result = await bybit.place_order(
            symbol=ticker, side=close_side, qty=size, order_type="Market", reduce_only=True,
        )

        if result.get("error"):
            logger.error("gate_exit_failed", ticker=ticker, error=result["error"])
            return

        # Verify fill before recording PnL
        order_id = result.get("order_id") or result.get("orderId")
        fill_price = float(pos_data.get("markPrice", 0) or pos_data.get("lastPrice", 0) or 0)
        if order_id and sor:
            try:
                verification = await sor.verify_close_filled(ticker, order_id, timeout=15)
                if verification.get("filled") and verification.get("fill_price"):
                    fill_price = float(verification["fill_price"])
                else:
                    logger.warning("gate_exit_fill_not_verified", ticker=ticker,
                                   order_id=order_id, reason=verification.get("reason"))
            except Exception as e:
                logger.warning("gate_exit_verify_failed", ticker=ticker, error=str(e))

        # Update DB and record closed trade in a SINGLE session — one connection checkout
        async with _async_session() as db_session:
            try:
                db_result = await db_session.execute(
                    select(CryptoPosition).where(
                        CryptoPosition.id == position_id,
                        CryptoPosition.status == "OPEN",
                    )
                )
                db_pos = db_result.scalar_one_or_none()
                if db_pos:
                    db_pos.status = "CLOSED"
                    db_pos.last_synced_at = datetime.utcnow()

                entry_price = float(pos_data.get("avgPrice", 0) or 0)
                pnl_per_unit = (fill_price - entry_price) if side == "Buy" else (entry_price - fill_price)
                realized_pnl = pnl_per_unit * size
                pnl_pct = (pnl_per_unit / entry_price * 100) if entry_price else gain_pct

                closed_trade = ClosedPaperTrade(
                    ticker=ticker,
                    market="CRYPTO",
                    side="LONG" if side == "Buy" else "SHORT",
                    quantity=Decimal(str(size)),
                    entry_price=Decimal(str(entry_price)),
                    exit_price=Decimal(str(fill_price)),
                    realized_pnl=Decimal(str(round(realized_pnl, 4))),
                    realized_pnl_pct=Decimal(str(round(pnl_pct, 4))),
                    exit_reason=f"performance_gate: {reason}",
                )
                db_session.add(closed_trade)
                await db_session.commit()

                from src.metrics.crypto_metrics import record_trade_close
                record_trade_close(
                    realized_pnl,
                    "win" if realized_pnl > 0 else "loss",
                    ticker=ticker,
                    exit_price=fill_price,
                    closed_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                )
            except Exception as e:
                await db_session.rollback()
                logger.warning("gate_exit_db_write_failed", ticker=ticker, error=str(e))

        # Clean up gate tracking data
        from src.risk.performance_gate import PerformanceGate
        gate = PerformanceGate(self.redis_client)
        await gate.mark_position_closed(position_id)

        logger.info("gate_exit_executed", ticker=ticker, size=size, reason=reason, gain_pct=gain_pct)

    async def _job_check_circuit_breakers(self):
        start = time.time()
        try:
            from src.risk.circuit_breaker import CircuitBreakerManager
            bybit = self.mcp._get_bybit()
            cb = CircuitBreakerManager(self.redis_client, bybit)
            triggered = await cb.check_all()
            JOB_LAST_RUN.labels(job_id="crypto_circuit_breakers").set(time.time())
            JOB_DURATION.labels(job_id="crypto_circuit_breakers").observe(time.time() - start)
            if triggered:
                logger.warning("circuit_breaker_triggered", details=triggered)
        except Exception as e:
            JOB_ERRORS.labels(job_id="crypto_circuit_breakers").inc()
            logger.error("circuit_breaker_job_failed", error=str(e))

    async def _job_scan_funding(self):
        """Funding capture strategy: scan for extreme funding rates and generate signals."""
        start = time.time()
        try:
            from src.strategies.funding_capture import FundingCaptureStrategy
            from src.risk.funding_tracker import FundingTracker
            from src.risk.sor import SmartOrderRouter

            bybit = self.mcp._get_bybit()
            tracker = FundingTracker(bybit)
            strategy = FundingCaptureStrategy(tracker, bybit)
            sor = SmartOrderRouter(bybit, oms=self.oms)

            # Get open positions for concurrency check
            positions = await bybit.get_positions()
            open_positions = positions if positions else []

            # Scan for funding opportunities
            signals = await strategy.scan(open_positions)

            for signal in signals:
                try:
                    # Emergency check
                    from src.risk import emergency
                    if await emergency.is_active():
                        break

                    # Evaluate through risk gate
                    from src.risk.crypto_risk_manager import CryptoRiskManager
                    risk_mgr = CryptoRiskManager(mcp=self.mcp, redis_client=self.redis_client)
                    risk_result = await risk_mgr.evaluate(
                        signal=signal,
                        open_positions=open_positions,
                        wallet_balance=0,  # will be fetched by risk manager if needed
                    )

                    if not risk_result.get("approved"):
                        logger.info("funding_signal_rejected",
                                    ticker=signal["ticker"],
                                    reason=risk_result.get("reason"))
                        continue

                    # Execute via SOR
                    result = await sor.execute_order(signal, risk_result)
                    if result.get("success"):
                        logger.info("funding_position_opened",
                                    ticker=signal["ticker"],
                                    direction=signal["direction"],
                                    rate=signal.get("_funding_annualized"))
                    else:
                        logger.warning("funding_execute_failed",
                                       ticker=signal["ticker"],
                                       error=result.get("error"))

                except Exception as e:
                    logger.error("funding_signal_failed",
                                 ticker=signal.get("ticker"), error=str(e))

            JOB_LAST_RUN.labels(job_id="funding_capture").set(time.time())
            JOB_DURATION.labels(job_id="funding_capture").observe(time.time() - start)
            if signals:
                logger.info("funding_scan_done", signals=len(signals))

        except Exception as e:
            JOB_ERRORS.labels(job_id="funding_capture").inc()
            logger.error("funding_scan_job_failed", error=str(e))

    async def _job_enforce_funding_limit(self):
        start = time.time()
        try:
            from src.risk.funding_tracker import FundingTracker
            bybit = self.mcp._get_bybit()
            tracker = FundingTracker(bybit)
            rates = await tracker.get_current_rates()
            breached = [r for r in rates if r.get("alert")]
            JOB_LAST_RUN.labels(job_id="funding_limit").set(time.time())
            JOB_DURATION.labels(job_id="funding_limit").observe(time.time() - start)
            if breached:
                logger.warning("funding_limit_breached", details=breached)
        except Exception as e:
            JOB_ERRORS.labels(job_id="funding_limit").inc()
            logger.error("funding_limit_job_failed", error=str(e))

    async def _job_reconcile_positions(self):
        start = time.time()
        try:
            from src.risk.position_sync import PositionReconciler
            bybit = self.mcp._get_bybit()
            reconciler = PositionReconciler(bybit)
            result = await reconciler.reconcile()
            JOB_LAST_RUN.labels(job_id="reconciliation").set(time.time())
            JOB_DURATION.labels(job_id="reconciliation").observe(time.time() - start)
            if result:
                logger.info("reconciliation_done", drifts=result)
        except Exception as e:
            JOB_ERRORS.labels(job_id="reconciliation").inc()
            logger.error("reconciliation_job_failed", error=str(e))

    async def _job_liquidity_check(self):
        start = time.time()
        try:
            bybit = self.mcp._get_bybit()
            from src.risk.liquidity import LiquidityMonitor
            monitor = LiquidityMonitor(bybit)
            
            universe = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
            if self.universe_engine:
                try:
                    universe = await self.universe_engine.get_current()
                except Exception as e:
                    logger.warning("liquidity_universe_fetch_failed", error=str(e))
            
            for ticker in universe:
                liq = await monitor.check_liquidity(ticker, "BUY")
                if not liq["can_trade"]:
                    logger.warning("liquidity_alert", ticker=ticker, reason=liq["reason"])
            JOB_LAST_RUN.labels(job_id="liquidity_check").set(time.time())
            JOB_DURATION.labels(job_id="liquidity_check").observe(time.time() - start)
            logger.info("liquidity_check_done")
        except Exception as e:
            JOB_ERRORS.labels(job_id="liquidity_check").inc()
            logger.error("liquidity_check_job_failed", error=str(e))

    async def _job_oms_cleanup(self):
        start = time.time()
        try:
            if self.oms:
                stuck = await self.oms.cleanup_stuck_orders()
                partial = await self.oms.handle_partial_fills()
                await self.oms.sync_from_exchange()
                JOB_LAST_RUN.labels(job_id="oms_cleanup").set(time.time())
                JOB_DURATION.labels(job_id="oms_cleanup").observe(time.time() - start)
                logger.info("oms_cleanup_done",
                          stuck_cancelled=len(stuck),
                          partial_cancelled=len(partial))
        except Exception as e:
            JOB_ERRORS.labels(job_id="oms_cleanup").inc()
            logger.error("oms_cleanup_failed", error=str(e))

    async def _job_kill_switch(self):
        start = time.time()
        try:
            from src.risk import emergency
            if await emergency.is_active():
                JOB_LAST_RUN.labels(job_id="kill_switch").set(time.time())
                return
            bybit = self.mcp._get_bybit()
            positions = await bybit.get_positions()
            if not positions:
                JOB_LAST_RUN.labels(job_id="kill_switch").set(time.time())
                return
            total_pnl = sum(float(p.get("unrealized_pnl", 0) or 0) for p in positions)
            wallet = await bybit.get_wallet_balance("USDT")
            total_equity = float(wallet.get("balance", 0)) if isinstance(wallet, dict) else 0
            if total_equity > 0 and total_pnl < 0:
                loss_pct = abs(total_pnl) / total_equity * 100
                DAILY_LOSS_PCT.set(loss_pct)
                if loss_pct >= settings.CRYPTO_DAILY_LOSS_LIMIT_PCT:
                    await emergency.activate(
                        reason=f"Crypto daily loss {loss_pct:.1f}% exceeds limit {settings.CRYPTO_DAILY_LOSS_LIMIT_PCT}%",
                        operator="kill_switch_job",
                    )
                    logger.warning("kill_switch_activated", loss_pct=loss_pct)
            JOB_LAST_RUN.labels(job_id="kill_switch").set(time.time())
            JOB_DURATION.labels(job_id="kill_switch").observe(time.time() - start)
        except Exception as e:
            JOB_ERRORS.labels(job_id="kill_switch").inc()
            logger.error("kill_switch_failed", error=str(e))

    # AODE Job Methods
    async def _job_aode_discovery(self):
        """AODE token discovery cycle (feature-flagged)."""
        from src.architecture.feature_flags import flags
        if not flags.is_enabled("aode_discovery_enabled"):
            return
        try:
            from src.research.research_orchestrator import ResearchOrchestrator
            orch = ResearchOrchestrator(cache=self.cache, bybit_client=self.mcp._get_bybit())
            result = await orch.run_discovery_cycle()
            logger.info("aode_discovery_done", result=result)
        except Exception as e:
            logger.error("aode_discovery_failed", error=str(e))

    async def _job_aode_research(self):
        """AODE research scoring cycle (feature-flagged)."""
        from src.architecture.feature_flags import flags
        if not flags.is_enabled("aode_research_enabled"):
            return
        try:
            from src.research.research_orchestrator import ResearchOrchestrator
            orch = ResearchOrchestrator(cache=self.cache)
            result = await orch.run_research_cycle()
            logger.info("aode_research_done", result=result)
        except Exception as e:
            logger.error("aode_research_failed", error=str(e))

    async def _job_aode_monitoring(self):
        """AODE monitoring cycle (feature-flagged)."""
        from src.architecture.feature_flags import flags
        if not flags.is_enabled("aode_monitoring_enabled"):
            return
        try:
            from src.research.monitoring_engine import MonitoringEngine
            engine = MonitoringEngine(cache=self.cache)
            result = await engine.run_monitoring_cycle()
            logger.info("aode_monitoring_done", result=result)
        except Exception as e:
            logger.error("aode_monitoring_failed", error=str(e))

    async def shutdown(self):
        logger.info("shutting_down")
        from src.architecture.events import event_bus as _event_bus
        await _event_bus.stop()
        if hasattr(self, 'sl_engine') and self.sl_engine:
            await self.sl_engine.stop()
        if hasattr(self, 'ws_manager') and self.ws_manager:
            await self.ws_manager.stop()
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        if self.mcp:
            await self.mcp.close()
        if self.redis_client:
            await self.redis_client.close()
        await close_db()
        logger.info("shutdown_complete")

    async def run(self):
        await self.startup()
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: self._shutdown.set())

        logger.info("scheduler_running", jobs=len(self.scheduler.get_jobs()))

        # Run uvicorn in a dedicated thread so APScheduler/LLM jobs
        # cannot starve the HTTP server of event-loop time.
        import threading

        config = uvicorn.Config(app, host="0.0.0.0", port=8001, log_level="warning")
        server = uvicorn.Server(config)

        def _run_uvicorn():
            asyncio.run(server.serve())

        uvicorn_thread = threading.Thread(target=_run_uvicorn, daemon=True, name="uvicorn-server")
        uvicorn_thread.start()
        logger.info("uvicorn_thread_started", port=8001)

        if self.universe_engine:
            loop.create_task(self.universe_engine.listen_profile_changes())
        if self.ws_manager:
            loop.create_task(self.ws_manager.run())
        if self.sl_engine:
            loop.create_task(self.sl_engine.run())

        await self._shutdown.wait()
        # uvicorn is daemon thread — will die with the process
        await self.shutdown()


def main():
    setup_logging()
    karsa = CryptoKarsaApp()
    asyncio.run(karsa.run())


if __name__ == "__main__":
    main()
