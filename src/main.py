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

        # Start health check HTTP server
        import threading
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import json

        karsa_app = self  # Capture reference to KarsaApp instance

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/health/scheduler':
                    try:
                        jobs = []
                        for job in karsa_app.scheduler.get_jobs():
                            next_run = job.next_run_time.isoformat() if job.next_run_time else None
                            jobs.append({"id": job.id, "name": job.name, "next_run": next_run})
                        data = {
                            "status": "running" if karsa_app.scheduler.running else "stopped",
                            "jobs": jobs,
                            "job_count": len(jobs)
                        }
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps(data).encode())
                    except Exception as e:
                        self.send_response(500)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": str(e)}).encode())
                elif self.path == '/health':
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "ok"}).encode())
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass  # Suppress HTTP logs

        def run_health_server():
            server = HTTPServer(('0.0.0.0', 8080), HealthHandler)
            server.serve_forever()

        threading.Thread(target=run_health_server, daemon=True).start()
        logger.info("health_server_started", port=8080)

        logger.info("karsa_ready")

    def _register_jobs(self):
        """Register all periodic jobs.

        Market Hours (UTC):
        - IDX: 09:00-16:00 WIB = 02:00-09:00 UTC (lunch 12:00-13:30 WIB = 05:00-06:30 UTC)
        - US: 09:30-16:00 ET = 13:30-20:00 UTC
        """
        s = self.scheduler

        # --- IDX MARKET ---
        # IDX Morning Session: 09:00-12:00 WIB (02:00-05:00 UTC), every 30 min
        s.add_job(
            self._job_scan_idx,
            "cron", day_of_week="mon-fri", hour="2-4", minute="0,30",
            id="scan_idx_morning", name="IDX Market Scan (Morning)",
            replace_existing=True, misfire_grace_time=300,
        )

        # IDX Afternoon Session: 13:30-16:00 WIB (06:30-09:00 UTC), every 30 min
        s.add_job(
            self._job_scan_idx,
            "cron", day_of_week="mon-fri", hour="6-8", minute="0,30",
            id="scan_idx_afternoon", name="IDX Market Scan (Afternoon)",
            replace_existing=True, misfire_grace_time=300,
        )

        # IDX EOD Review: 16:15 WIB (09:15 UTC)
        s.add_job(
            self._job_eod_review,
            "cron", day_of_week="mon-fri", hour=9, minute=15,
            id="eod_review_idx", name="IDX EOD Review",
            replace_existing=True, misfire_grace_time=600,
        )

        # --- US MARKET ---
        # US Market Scan: 09:30-16:00 ET (13:30-20:00 UTC), every 30 min
        s.add_job(
            self._job_scan_us_etf,
            "cron", day_of_week="mon-fri", hour="13-19", minute="0,30",
            id="scan_us_etf", name="US & ETF Market Scan",
            replace_existing=True, misfire_grace_time=300,
        )

        # Pre-Market Battle Plan: 09:25 ET (14:25 UTC) weekdays
        s.add_job(
            self._job_premarket_battleplan,
            "cron", day_of_week="mon-fri", hour=14, minute=25,
            id="premarket_battleplan", name="Pre-Market Battle Plan",
            replace_existing=True, misfire_grace_time=600,
        )

        # US EOD Review: 16:15 ET (21:15 UTC)
        s.add_job(
            self._job_eod_review,
            "cron", day_of_week="mon-fri", hour=21, minute=15,
            id="eod_review_us", name="US EOD Review",
            replace_existing=True, misfire_grace_time=600,
        )

        # --- SHARED ---
        # Paper position price updates: every 5 min during market hours
        # IDX: 02:00-09:00 UTC, US: 13:30-20:00 UTC
        s.add_job(
            self._job_update_paper_positions,
            "cron", day_of_week="mon-fri", hour="2-9,13-20", minute="*/5",
            id="paper_update", name="Paper Position Price Update",
            replace_existing=True, misfire_grace_time=120,
        )

        # Kill Switch: every 5 min during market hours
        s.add_job(
            self._job_kill_switch,
            "cron", day_of_week="mon-fri", hour="2-9,13-20", minute="*/5",
            id="kill_switch", name="Daily Loss Kill Switch",
            replace_existing=True, misfire_grace_time=60,
        )

        # Flush OHLCV cache: hourly
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

    async def _job_update_paper_positions(self):
        """Update current prices for paper positions AND portfolio."""
        logger.info("price_update_started")
        try:
            from src.models.tables import PaperPosition, PortfolioState
            from sqlalchemy import select

            async with async_session() as session:
                # Update paper positions
                result = await session.execute(select(PaperPosition))
                positions = result.scalars().all()

                for pos in positions:
                    quote = await self.mcp.get_quote(pos.ticker, pos.market)
                    if quote and not quote.get("error"):
                        pos.current_price = quote.get("price")
                        if pos.entry_price and pos.current_price:
                            if pos.side == "LONG":
                                pos.unrealized_pnl = (pos.current_price - pos.entry_price) * pos.quantity
                            else:
                                pos.unrealized_pnl = (pos.entry_price - pos.current_price) * pos.quantity
                            pos.unrealized_pnl_pct = (pos.unrealized_pnl / (pos.entry_price * pos.quantity)) * 100

                # Update portfolio positions
                port_result = await session.execute(select(PortfolioState))
                portfolio = port_result.scalars().all()

                for p in portfolio:
                    quote = await self.mcp.get_quote(p.ticker, p.market)
                    if quote and not quote.get("error"):
                        p.current_price = quote.get("price")
                        if p.avg_cost and p.current_price:
                            p.unrealized_pnl = (p.current_price - p.avg_cost) * p.quantity

                await session.commit()
            logger.info("price_update_done", paper=len(positions), portfolio=len(portfolio))
        except Exception as e:
            logger.error("paper_update_failed", error=str(e))

    async def _job_expire_approvals(self):
        """Expire stale approvals and mark signals as EXPIRED."""
        pass

    async def _job_premarket_battleplan(self):
        """Generate and push pre-market battle plan to Telegram."""
        logger.info("premarket_battleplan_started")
        # ponytail: call Orchestrator.generate_battleplan(), format, and send via bot_token/chat_id.
        # Add when Telegram broadcast mechanism is centralized.
        logger.info("premarket_battleplan_done")

    async def _job_eod_review(self):
        """Generate and push EOD review to Telegram."""
        logger.info("eod_review_started")
        # ponytail: aggregate closed paper trades today, send summary to Telegram.
        logger.info("eod_review_done")

    async def _job_kill_switch(self):
        """Check if daily loss limit is breached."""
        logger.info("kill_switch_check_started")
        try:
            from src.models.tables import ClosedPaperTrade
            from sqlalchemy import select, func, cast, Date
            from datetime import datetime

            async with async_session() as session:
                today = datetime.utcnow().date()
                result = await session.execute(
                    select(func.sum(ClosedPaperTrade.realized_pnl_pct))
                    .where(cast(ClosedPaperTrade.exit_date, Date) == today)
                )
                daily_pnl_pct = result.scalar() or 0.0

                # If daily loss exceeds 1.5%, halt trading
                if daily_pnl_pct <= -1.5:
                    logger.warning("kill_switch_activated", daily_pnl_pct=daily_pnl_pct)
                    # ponytail: push alert to Telegram and set REDIS flag 'HALT_TRADING'
        except Exception as e:
            logger.error("kill_switch_failed", error=str(e))

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
