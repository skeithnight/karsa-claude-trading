"""Karsa Trading System - Entry Point & Scheduler"""

import asyncio
import signal
import sys

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

# FastAPI app for health endpoints
app = FastAPI(title="Karsa Orchestrator", version="0.1.0")


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
        jobstores = {
            "default": MemoryJobStore(),
        }
        self.scheduler = AsyncIOScheduler(jobstores=jobstores)
        self._register_jobs()
        self.scheduler.start()
        logger.info("scheduler_started")

        # Wire health endpoints into FastAPI
        self._register_health_routes()

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

    def _register_jobs(self):
        """Register all periodic jobs.

        Market Hours (UTC):
        - IDX: 09:00-16:00 WIB = 02:00-09:00 UTC (lunch 12:00-13:30 WIB = 05:00-06:30 UTC)
        - US: 09:30-16:00 ET = 13:30-20:00 UTC
        """
        s = self.scheduler

        # --- IDX MARKET ---
        s.add_job(
            self._job_scan_idx,
            "cron", day_of_week="mon-fri", hour="2-4", minute="0,30",
            id="scan_idx_morning", name="IDX Market Scan (Morning)",
            replace_existing=True, misfire_grace_time=300,
        )

        s.add_job(
            self._job_scan_idx,
            "cron", day_of_week="mon-fri", hour="6-8", minute="0,30",
            id="scan_idx_afternoon", name="IDX Market Scan (Afternoon)",
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

                if daily_pnl_pct <= -1.5:
                    activated = await emergency.activate(
                        reason=f"Daily loss limit breached: {daily_pnl_pct:+.2f}%",
                        operator="system-kill-switch",
                    )
                    if activated:
                        logger.warning("kill_switch_activated", daily_pnl_pct=daily_pnl_pct)
                        # Send Telegram alert
                        try:
                            import httpx
                            async with httpx.AsyncClient(timeout=10) as client:
                                await client.post(
                                    f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage",
                                    json={
                                        "chat_id": settings.TELEGRAM_CHAT_ID,
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

    async def _job_flush_cache(self):
        """Flush cached OHLCV data from Redis to Postgres."""
        logger.info("cache_flush_started")
        # ponytail: iterate OHLCV keys in Redis, bulk upsert to ohlcv_cache table.
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
        """Main run loop — starts uvicorn + scheduler."""
        await self.startup()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: self._shutdown.set())

        logger.info("scheduler_running", jobs=len(self.scheduler.get_jobs()))

        # Run uvicorn in background task
        config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
        server = uvicorn.Server(config)
        loop.create_task(server.serve())

        # Keep running until shutdown signal
        await self._shutdown.wait()
        await server.shutdown()
        await self.shutdown()


def main():
    setup_logging()
    karsa = KarsaApp()
    asyncio.run(karsa.run())


if __name__ == "__main__":
    main()
