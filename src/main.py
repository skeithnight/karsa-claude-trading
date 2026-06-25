"""Karsa Trading System - Entry Point & Scheduler"""

import asyncio
import signal
import sys

import redis.asyncio as redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from src.config import settings
from src.models.database import init_db, close_db, DATABASE_URL
from src.data.cache import CacheManager
from src.data.mcp_client import MCPClient
from src.data.idx_adapter import IDXDataAdapter
from src.agents.orchestrator import Orchestrator, IDX_UNIVERSE, US_UNIVERSE, ETF_UNIVERSE
from src.bot.approval import ApprovalManager
from src.models.database import async_session
from src.utils.logging import setup_logging, get_logger
from src.utils.rate_limit import RateLimiter

logger = get_logger("main")

# APScheduler job store: backed by Postgres so jobs survive container restarts.
# The sync URL is derived by dropping "+asyncpg" for APScheduler's SQLAlchemy engine.
SYNC_DB_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


class KarsaApp:
    """Main application container with APScheduler integration."""

    def __init__(self):
        self.redis_client: redis.Redis | None = None
        self.cache: CacheManager | None = None
        self.mcp: MCPClient | None = None
        self.idx_adapter: IDXDataAdapter | None = None
        self.rate_limiter: RateLimiter | None = None
        self.orchestrator: Orchestrator | None = None
        self.approval_manager: ApprovalManager | None = None
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

        self.mcp = MCPClient(self.cache)
        self.idx_adapter = IDXDataAdapter(self.cache)

        # Agents & orchestrator
        self.orchestrator = Orchestrator(self.mcp, self.idx_adapter, self.cache, self.rate_limiter)
        self.approval_manager = ApprovalManager(self.cache, async_session)
        logger.info("orchestrator_ready")

        # APScheduler with Postgres job store
        jobstores = {
            "default": SQLAlchemyJobStore(url=SYNC_DB_URL),
        }
        self.scheduler = AsyncIOScheduler(jobstores=jobstores)
        self._register_jobs()
        self.scheduler.start()
        logger.info("scheduler_started")

        logger.info("karsa_ready")

    def _register_jobs(self):
        """Register all periodic jobs."""
        s = self.scheduler

        # Market scans — cron-style, only fire when market is open.
        # IDX: 09:30-15:30 WIB (02:30-08:30 UTC), every 30 min
        s.add_job(
            self._job_scan_idx,
            "cron", day_of_week="mon-fri", hour="2-8", minute="0,30",
            id="scan_idx", name="IDX Market Scan",
            replace_existing=True, misfire_grace_time=300,
        )

        # US: 09:30-15:30 ET (13:30-19:30 UTC), every 30 min
        s.add_job(
            self._job_scan_us_etf,
            "cron", day_of_week="mon-fri", hour="13-19", minute="0,30",
            id="scan_us_etf", name="US & ETF Market Scan",
            replace_existing=True, misfire_grace_time=300,
        )

        # State reconciliation: daily at 08:00 UTC (15:00 WIB / 04:00 ET)
        s.add_job(
            self._job_reconcile_positions,
            "cron", hour=8, minute=0,
            id="reconcile", name="Position Reconciliation",
            replace_existing=True, misfire_grace_time=600,
        )

        # Expire stale approvals: every 5 min
        s.add_job(
            self._job_expire_approvals,
            "interval", minutes=5,
            id="expire_approvals", name="Expire Stale Approvals",
            replace_existing=True,
        )

        # Flush OHLCV cache to Postgres: hourly
        s.add_job(
            self._job_flush_cache,
            "cron", minute=5,
            id="flush_cache", name="Flush OHLCV Cache",
            replace_existing=True,
        )

        logger.info("jobs_registered", count=len(self.scheduler.get_jobs()))

    # --- Job implementations ---

    async def _job_scan_idx(self):
        """Scan IDX market during IDX trading hours."""
        try:
            from src.utils.market_hours import is_idx_open
            if not is_idx_open():
                logger.info("idx_market_closed_skip")
                return
            signals = await self.orchestrator._scan_market(
                "IDX", self.orchestrator.idx_agent, IDX_UNIVERSE
            )
            logger.info("idx_scan_done", signals=len(signals))
        except Exception as e:
            logger.error("idx_scan_failed", error=str(e))

    async def _job_scan_us_etf(self):
        """Scan US + ETF markets during US trading hours."""
        try:
            from src.utils.market_hours import is_us_open
            if not is_us_open():
                logger.info("us_market_closed_skip")
                return
            # Run both in parallel
            await asyncio.gather(
                self.orchestrator._scan_market("US", self.orchestrator.us_agent, US_UNIVERSE),
                self.orchestrator._scan_market("ETF", self.orchestrator.etf_agent, ETF_UNIVERSE),
                return_exceptions=True,
            )
            logger.info("us_etf_scan_done")
        except Exception as e:
            logger.error("us_etf_scan_failed", error=str(e))

    async def _job_reconcile_positions(self):
        """Sync portfolio positions from brokers to Postgres."""
        logger.info("reconcile_started")
        # ponytail: implement broker.get_positions() → upsert portfolio_state
        # Requires broker instances. Deferred to when brokers are wired up in main.
        logger.info("reconcile_done")

    async def _job_expire_approvals(self):
        """Expire stale approvals and mark signals as EXPIRED."""
        try:
            await self.approval_manager.expire_stale_approvals()
        except Exception as e:
            logger.error("expire_approvals_failed", error=str(e))

    async def _job_flush_cache(self):
        """Flush cached OHLCV data from Redis to Postgres."""
        logger.info("cache_flush_started")
        # ponytail: iterate OHLCV keys in Redis, bulk upsert to ohlcv_cache table.
        # Deferred to when we have enough live data flowing.
        logger.info("cache_flush_done")

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("shutting_down")
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        if self.mcp:
            await self.mcp.close()
        if self.idx_adapter:
            await self.idx_adapter.close()
        if self.redis_client:
            await self.redis_client.close()
        await close_db()
        logger.info("shutdown_complete")

    async def run(self):
        """Main run loop."""
        await self.startup()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: self._shutdown.set())

        logger.info("scheduler_running", jobs=len(self.scheduler.get_jobs()))

        # Keep running until shutdown signal
        await self._shutdown.wait()
        await self.shutdown()


def main():
    setup_logging()
    app = KarsaApp()
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
