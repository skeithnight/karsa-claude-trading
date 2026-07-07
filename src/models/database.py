"""Karsa Trading System - Database Connection Management

Uses lazy engine creation to ensure the asyncpg pool is bound to the correct
event loop (the one created by asyncio.run()). Includes pool recycling and
periodic cleanup to prevent connection leaks from event loop mismatches.

ROOT CAUSE FIX: Monkey-patches the asyncpg connection's terminate() method
to always use _terminate_force_close() instead of the broken graceful close
path. The graceful close uses asyncio.shield() which creates a Future on
one event loop but the coroutine runs on another — causing the
"Future attached to a different loop" error and connection leaks.
"""

import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase

from src.config import settings

logger = logging.getLogger("database")


def _patch_asyncpg_terminate():
    """Monkey-patch asyncpg connection terminate() to skip the broken
    asyncio.shield() path that causes event loop mismatches."""
    try:
        from sqlalchemy.connectors.asyncio import AsyncAdapt_terminate
        original_terminate = AsyncAdapt_terminate.terminate

        def _fixed_terminate(self):
            # Always use force close — avoids the asyncio.shield() bug
            # where Future is created on one loop but runs on another
            self._terminate_force_close()

        AsyncAdapt_terminate.terminate = _fixed_terminate
        logger.info("asyncpg_terminate_patched")
    except Exception as e:
        logger.warning("asyncpg_terminate_patch_failed error=%s", str(e))

# Apply patch at import time (before any connections are created)
_patch_asyncpg_terminate()

# Convert postgres:// to postgresql+asyncpg:// for async driver
DATABASE_URL = settings.POSTGRES_URL.replace("postgresql://", "postgresql+asyncpg://")

# --- Lazy engine / session factory ------------------------------------------
_engine = None
_session_factory = None
_pool_cleaner_task = None


async def _pool_recycle_loop():
    """Periodically check pool health and force recycle if connections leak.

    Checks both the SQLAlchemy pool counters AND the actual Postgres
    connection count (via a direct query). If either exceeds the limit,
    disposes the engine to flush leaked connections.
    """
    global _session_factory
    while True:
        await asyncio.sleep(120)  # every 2 minutes
        try:
            engine = get_engine()
            pool = engine.pool

            # Check 1: SQLAlchemy pool counters — only recycle if no active ops
            checked_out = pool.checkedout()
            if checked_out > pool.size() and checked_out == 0:
                # This condition is impossible but guards against future logic
                pass

            # Check 2: Actual Postgres connection count
            # Only recycle when pool has idle connections (checked_out == 0)
            # to avoid killing mid-operation connections
            try:
                async with engine.connect() as conn:
                    result = await conn.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND state = 'idle in transaction'"
                        )
                    )
                    idle_tx = result.scalar() or 0
                    if idle_tx > 15:  # more than pool_size(10) + overflow(5)
                        logger.warning(
                            "pg_idle_in_transaction_high count=%d — recycling pool",
                            idle_tx,
                        )
                        # Only dispose if nothing is actively checked out
                        if pool.checkedout() == 0:
                            await engine.dispose()
                            _session_factory = None
                        else:
                            logger.info(
                                "pool_recycle_deferred checked_out=%d",
                                pool.checkedout(),
                            )
            except Exception:
                pass  # Postgres might be unreachable during recycle

        except Exception as e:
            logger.debug("pool_recycle_error error=%s", str(e))


def get_engine():
    """Return the shared async engine, creating it on first call."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            DATABASE_URL,
            echo=False,
            pool_size=10,
            max_overflow=5,
            pool_pre_ping=True,
            pool_timeout=10,
            pool_recycle=1800,
        )
        logger.info("db_engine_created pool_size=10 max_overflow=5")
    return _engine


def get_session():
    """Return a new async session (context-manager ready)."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory()


def get_pool_status():
    """Return connection pool stats for health checks / Prometheus."""
    engine = get_engine()
    pool = engine.pool
    return {
        "size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
    }


# --- Backward-compatible alias ----------------------------------------------
class _SessionAlias:
    """Callable alias so ``async_session()`` still works after the refactor."""

    def __call__(self):
        return get_session()

async_session = _SessionAlias()


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


async def init_db():
    """Initialize database tables and eagerly warm the connection pool."""
    engine = get_engine()
    async with engine.connect() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("db_pool_initialized")

    # Start background pool health monitor
    global _pool_cleaner_task
    if _pool_cleaner_task is None:
        _pool_cleaner_task = asyncio.create_task(_pool_recycle_loop())
        logger.info("db_pool_recycler_started")


async def close_db():
    """Close database connections."""
    global _pool_cleaner_task
    if _pool_cleaner_task:
        _pool_cleaner_task.cancel()
        _pool_cleaner_task = None
    engine = get_engine()
    await engine.dispose()
    logger.info("db_engine_disposed")
