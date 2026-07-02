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

        self.mcp = MCPClient(self.cache)
        self.orchestrator = Orchestrator(self.mcp, self.cache, self.rate_limiter)

        # Risk profile + dynamic universe engine
        from src.risk.profile_manager import RiskProfileManager
        from src.advisory.crypto_universe import UniverseEngine
        self.profile_manager = RiskProfileManager(self.redis_client)
        self.orchestrator.profile_manager = self.profile_manager
        bybit = self.mcp._get_bybit()
        self.universe_engine = UniverseEngine(bybit, self.redis_client, self.profile_manager)
        self.orchestrator.universe_engine = self.universe_engine

        # Execution engine modules
        from src.execution.websocket_manager import WebSocketManager
        from src.execution.sl_engine import StopLossEngine
        from src.execution.oms import OrderManagementSystem
        from src.risk.portfolio_allocator import PortfolioAllocator
        self.ws_manager = WebSocketManager(self.redis_client, bybit)
        self.sl_engine = StopLossEngine(self.redis_client, bybit)
        self.oms = OrderManagementSystem(self.redis_client, bybit)
        self.portfolio_allocator = PortfolioAllocator(self.redis_client)
        self.orchestrator.portfolio_allocator = self.portfolio_allocator

        # Confidence calibration
        from src.risk.calibration_engine import ConfidenceCalibrator
        self.calibrator = ConfidenceCalibrator()
        self.orchestrator.calibrator = self.calibrator

        # Register Prometheus metrics (must import at startup so prometheus_client sees them)
        import src.metrics.crypto_metrics  # noqa: F401

        logger.info("orchestrator_ready")

        jobstores = {"default": MemoryJobStore()}
        self.scheduler = AsyncIOScheduler(jobstores=jobstores)
        self._register_jobs()
        self.scheduler.start()
        logger.info("scheduler_started")

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
            try:
                from sqlalchemy import text
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
                "checks": {"postgres": "ok" if db_ok else "FAIL", "redis": "ok" if redis_ok else "FAIL"},
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
                  id="scan_crypto", name="Crypto Market Scan (24/7)", replace_existing=True, misfire_grace_time=600)
        s.add_job(self._job_refresh_universe, "cron", minute="*/15",
                  id="refresh_universe", name="Crypto Universe Refresh (every 15m)", replace_existing=True, misfire_grace_time=600)
        s.add_job(self._job_monitor_crypto_positions, "cron", minute="*/15",
                  id="crypto_monitor", name="Crypto Position Monitor", replace_existing=True, misfire_grace_time=120)
        s.add_job(self._job_sync_crypto_funding, "cron", hour="0,8,16", minute=5,
                  id="crypto_funding", name="Crypto Funding Rate Sync", replace_existing=True, misfire_grace_time=300)
        s.add_job(self._job_crypto_pnl_snapshot, "cron", hour=0, minute=0,
                  id="crypto_pnl_snapshot", name="Crypto Daily PnL Snapshot", replace_existing=True, misfire_grace_time=600)
        s.add_job(self._job_sync_crypto_positions, "cron", minute="*/5",
                  id="crypto_position_sync", name="Crypto Position Sync", replace_existing=True, misfire_grace_time=120)

        # Lifecycle management
        s.add_job(self._job_update_trailing_stops, "cron", minute="*/5",
                  id="crypto_trailing_stops", name="Crypto Trailing Stop Update", replace_existing=True, misfire_grace_time=120)
        s.add_job(self._job_check_partial_exits, "cron", minute="*/2",
                  id="crypto_partial_exits", name="Crypto Partial Exit Check", replace_existing=True, misfire_grace_time=60)
        s.add_job(self._job_check_time_exits, "cron", hour="*", minute=30,
                  id="crypto_time_exits", name="Crypto Time-Based Exit Check", replace_existing=True, misfire_grace_time=300)
        s.add_job(self._job_check_circuit_breakers, "cron", minute="*/1",
                  id="crypto_circuit_breakers", name="Crypto Circuit Breaker Check", replace_existing=True, misfire_grace_time=60)
        s.add_job(self._job_enforce_funding_limit, "cron", hour="*", minute=20,
                  id="crypto_funding_limit", name="Crypto Funding Limit Enforcement", replace_existing=True, misfire_grace_time=300)
        s.add_job(self._job_reconcile_positions, "interval", seconds=60,
                  id="crypto_reconciliation", name="Crypto Position Reconciliation", replace_existing=True, misfire_grace_time=30)
        s.add_job(self._job_liquidity_check, "cron", minute="*/15",
                  id="crypto_liquidity", name="Crypto Liquidity Check", replace_existing=True, misfire_grace_time=120)
        s.add_job(self._job_oms_cleanup, "interval", minutes=2,
                  id="oms_cleanup", name="OMS Stuck Order Cleanup", replace_existing=True, misfire_grace_time=60)
        s.add_job(self._job_kill_switch, "cron", minute="*/5",
                  id="kill_switch", name="Crypto Kill Switch", replace_existing=True, misfire_grace_time=60)
        s.add_job(self._job_metrics_sync, "interval", seconds=60,
                  id="crypto_metrics_sync", name="Crypto Metrics Sync", replace_existing=True, misfire_grace_time=30)

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
        try:
            from src.metrics.crypto_metrics import (
                PORTFOLIO_EQUITY_USD, REDIS_CONNECTED, WARP_CONNECTED, 
                OPEN_POSITIONS, UNREALIZED_PNL_USD
            )
            # Update Redis Health
            redis_ok = await self.cache.ping() if self.cache else False
            REDIS_CONNECTED.set(1 if redis_ok else 0)

            bybit = self.mcp._get_bybit()
            # We assume WARP is OK if we can hit Bybit successfully
            wallet = await bybit.get_wallet_balance()
            if not wallet.get("error"):
                WARP_CONNECTED.set(1)
                equity = wallet.get("equity", wallet.get("balance", 0))
                PORTFOLIO_EQUITY_USD.set(float(equity))
            else:
                if "SOCKS" in wallet.get("error", "") or "unreachable" in wallet.get("error", ""):
                    WARP_CONNECTED.set(0)

            positions = await bybit.get_positions()
            if not isinstance(positions, dict) or not positions.get("error"):
                OPEN_POSITIONS.set(len(positions))
                unrealized = sum(float(p.get("unrealized_pnl", 0)) for p in positions)
                UNREALIZED_PNL_USD.set(unrealized)

            JOB_LAST_RUN.labels(job_id="metrics_sync").set(time.time())
            JOB_DURATION.labels(job_id="metrics_sync").observe(time.time() - start)
        except Exception as e:
            JOB_ERRORS.labels(job_id="metrics_sync").inc()
            logger.error("metrics_sync_failed", error=str(e))

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
            from src.advisory.performance_tracker import PerformanceTracker
            tracker = PerformanceTracker(self.redis_client)
            await tracker.snapshot()
            JOB_LAST_RUN.labels(job_id="crypto_pnl_snapshot").set(time.time())
            JOB_DURATION.labels(job_id="crypto_pnl_snapshot").observe(time.time() - start)
            logger.info("crypto_pnl_snapshot_done")
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
            bybit = self.mcp._get_bybit()
            manager = TrailingStopManager(bybit, self.redis_client)
            positions_data = await bybit.get_positions()
            if not positions_data:
                JOB_LAST_RUN.labels(job_id="crypto_trailing_stops").set(time.time())
                return
            positions = []
            for p in positions_data:
                if float(p.get("size", 0) or 0) > 0:
                    positions.append(CryptoPosition(
                        id=0, ticker=p.get("symbol", ""),
                        side=p.get("side", "Buy"),
                        entry_price=float(p.get("avgPrice", 0) or 0),
                        quantity=float(p.get("size", 0) or 0),
                        current_price=float(p.get("markPrice", 0) or 0),
                        status="OPEN",
                    ))
            if positions:
                await manager.update_trailing_stops(positions)
            JOB_LAST_RUN.labels(job_id="crypto_trailing_stops").set(time.time())
            JOB_DURATION.labels(job_id="crypto_trailing_stops").observe(time.time() - start)
            logger.info("trailing_stops_updated", count=len(positions))
        except Exception as e:
            JOB_ERRORS.labels(job_id="crypto_trailing_stops").inc()
            logger.error("trailing_stop_job_failed", error=str(e))

    async def _job_check_partial_exits(self):
        start = time.time()
        try:
            from src.risk.position_manager import PositionManager
            bybit = self.mcp._get_bybit()
            pm = PositionManager(bybit, self.redis_client)
            positions = await bybit.get_positions()
            if not positions:
                JOB_LAST_RUN.labels(job_id="crypto_partial_exits").set(time.time())
                return
            from src.models.tables import CryptoPosition
            crypto_positions = []
            for p in positions:
                if float(p.get("size", 0) or 0) > 0:
                    crypto_positions.append(CryptoPosition(
                        ticker=p.get("ticker"),
                        side=p.get("side"),
                        size=p.get("size"),
                        entry_price=p.get("entry_price"),
                        current_price=p.get("current_price"),
                        leverage=int(p.get("leverage", 1)),
                        liquidation_price=p.get("liquidation_price"),
                        unrealized_pnl=p.get("unrealized_pnl"),
                        stop_loss=p.get("stop_loss"),
                        take_profit=p.get("take_profit"),
                    ))
            actions = await pm.check_partial_exits(crypto_positions)
            for action in actions:
                await pm.execute_partial_exit(action)
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
            bybit = self.mcp._get_bybit()
            pm = PositionManager(bybit, self.redis_client)
            positions = await bybit.get_positions()
            if not positions:
                JOB_LAST_RUN.labels(job_id="crypto_time_exits").set(time.time())
                return
            from src.models.tables import CryptoPosition
            crypto_positions = [CryptoPosition(**p) for p in positions if float(p.get("size", 0) or 0) > 0]
            actions = await pm.check_time_exits(crypto_positions)
            for action in actions:
                await pm.execute_partial_exit(action)
            JOB_LAST_RUN.labels(job_id="crypto_time_exits").set(time.time())
            JOB_DURATION.labels(job_id="crypto_time_exits").observe(time.time() - start)
            if actions:
                logger.info("time_exits_executed", count=len(actions))
        except Exception as e:
            JOB_ERRORS.labels(job_id="crypto_time_exits").inc()
            logger.error("time_exit_job_failed", error=str(e))

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
                    universe = await self.universe_engine.get_universe()
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
                await self.oms.sync_from_exchange()
                JOB_LAST_RUN.labels(job_id="oms_cleanup").set(time.time())
                JOB_DURATION.labels(job_id="oms_cleanup").observe(time.time() - start)
                logger.info("oms_cleanup_done", stuck_cancelled=len(stuck))
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
            total_pnl = sum(float(p.get("unrealisedPnl", 0) or 0) for p in positions)
            total_equity = float(await bybit.get_wallet_balance("USDT") or 0)
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

    async def shutdown(self):
        logger.info("shutting_down")
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

        config = uvicorn.Config(app, host="0.0.0.0", port=8001, log_level="warning")
        server = uvicorn.Server(config)
        loop.create_task(server.serve())

        if self.universe_engine:
            loop.create_task(self.universe_engine.listen_profile_changes())
        if self.ws_manager:
            loop.create_task(self.ws_manager.run())
        if self.sl_engine:
            loop.create_task(self.sl_engine.run())

        await self._shutdown.wait()
        await server.shutdown()
        await self.shutdown()


def main():
    setup_logging()
    karsa = CryptoKarsaApp()
    asyncio.run(karsa.run())


if __name__ == "__main__":
    main()
