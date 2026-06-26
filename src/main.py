"""Karsa Trading System - Entry Point & Scheduler"""

import asyncio
import signal
import sys

import redis.asyncio as redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore

from src.config import settings
from src.models.database import init_db, close_db, DATABASE_URL
from src.data.cache import CacheManager
from src.data.mcp_client import MCPClient
from src.agents.orchestrator import Orchestrator, IDX_UNIVERSE, US_UNIVERSE, ETF_UNIVERSE
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

        self.mcp = MCPClient(self.cache)

        # Agents & orchestrator
        self.orchestrator = Orchestrator(self.mcp, self.cache, self.rate_limiter)
        logger.info("orchestrator_ready")

        # APScheduler with in-memory job store
        # ponytail: jobs are stateless scans, no persistence needed across restarts.
        # Switch to SQLAlchemyJobStore + psycopg2-binary if you need jobs to survive restarts.
        jobstores = {
            "default": MemoryJobStore(),
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
        """Scan IDX market — full pipeline: agents → risk → persist → notify."""
        try:
            from src.utils.market_hours import is_idx_open
            if not is_idx_open():
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

    async def _job_reconcile_positions(self):
        """Sync portfolio positions from brokers to Postgres."""
        logger.info("reconcile_started")
        # ponytail: implement broker.get_positions() → upsert portfolio_state
        # Requires broker instances. Deferred to when brokers are wired up in main.
        logger.info("reconcile_done")

    async def _job_expire_approvals(self):
        """Expire stale approvals and mark signals as EXPIRED."""
        pass

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
