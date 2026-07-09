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
from sqlalchemy.pool import NullPool
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase

from src.config import settings

logger = logging.getLogger("database")


def _patch_asyncpg_terminate():
    """Monkey-patch asyncpg connection terminate() to skip the broken graceful close.

    The bug: terminate() calls self.await_(asyncio.shield(self._terminate_graceful_close()))
    which creates a Future on one event loop but the coroutine runs on another.

    Fix: patch terminate() to use force close instead of the broken shield() path.
    """
    try:
        # Correct import path for SQLAlchemy 2.0+ asyncpg dialect
        from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_connection

        def _fixed_terminate(self):
            # Force close the underlying asyncpg connection to avoid asyncio.shield() bugs
            if self._connection:
                self._connection.terminate()
            self._started = False

        AsyncAdapt_asyncpg_connection.terminate = _fixed_terminate
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
_health_engine = None          # NullPool engine — never competes with main pool
_engine_lock = asyncio.Lock()  # Prevents two engines being created concurrently


def get_health_engine():
    """Return a dedicated NullPool engine for health/monitoring queries.

    Creates and closes a real connection on every use — never borrows from the
    main pool. This means the watchdog loop can query pg_stat_activity even
    when the main pool is fully saturated.
    """
    global _health_engine
    if _health_engine is None:
        _health_engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    return _health_engine


async def _pool_recycle_loop():
    """Periodically check pool health and force recycle if connections leak.

    Checks both the SQLAlchemy pool counters AND the actual Postgres
    connection count (via a direct query on the NullPool health engine).
    If either exceeds the limit, disposes the engine to flush leaked connections.
    """
    global _engine, _session_factory
    while True:
        await asyncio.sleep(120)  # every 2 minutes
        try:
            engine = get_engine()
            pool = engine.pool

            # Emit pool metrics to Prometheus
            try:
                from src.metrics.crypto_metrics import DB_POOL_CHECKED_OUT, DB_POOL_OVERFLOW
                DB_POOL_CHECKED_OUT.set(pool.checkedout())
                DB_POOL_OVERFLOW.set(pool.overflow())
            except Exception:
                pass

            # Check actual Postgres connection count using NullPool health engine
            # so this query NEVER steals a slot from the main pool
            try:
                health_engine = get_health_engine()
                async with health_engine.connect() as conn:
                    result = await conn.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND state IN ('idle', 'idle in transaction')"
                        )
                    )
                    idle_conns = result.scalar() or 0

                    # Calculate expected max: pool_size + max_overflow
                    expected_max = pool.size() + pool._max_overflow
                    leak_threshold = expected_max + 3  # small margin

                    logger.debug(
                        "pool_status idle=%d expected_max=%d checked_in=%d checked_out=%d overflow=%d",
                        idle_conns, expected_max, pool.checkedin(), pool.checkedout(), pool.overflow(),
                    )

                    # Dispose under lock so no concurrent coroutine creates
                    # a session factory pointing at the old (disposed) engine
                    if idle_conns > leak_threshold:
                        logger.warning(
                            "pg_idle_connections_high count=%d expected_max=%d — forcing pool dispose",
                            idle_conns, expected_max,
                        )
                        async with _engine_lock:
                            await engine.dispose()
                            _engine = None
                            _session_factory = None
                    elif pool.overflow() < 0:
                        logger.warning(
                            "pg_pool_overflow_negative overflow=%d — forcing pool dispose",
                            pool.overflow(),
                        )
                        async with _engine_lock:
                            await engine.dispose()
                            _engine = None
                            _session_factory = None

            except Exception:
                pass  # Postgres might be unreachable during recycle

        except Exception as e:
            logger.debug("pool_recycle_error error=%s", str(e))


async def _get_or_create_engine():
    """Return the shared async engine, creating it safely under a lock.

    Using a lock prevents two coroutines from simultaneously creating separate
    engine instances when the engine is None (e.g. after a dispose-reset).
    """
    global _engine
    async with _engine_lock:
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


def get_engine():
    """Return the shared async engine (sync accessor — engine must exist).

    Call init_db() at startup to guarantee the engine is created.
    After that, get_engine() is safe to call from any coroutine.
    """
    global _engine
    if _engine is None:
        # Fallback: create synchronously if called before init_db (shouldn't happen)
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
    engine = await _get_or_create_engine()
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
    global _pool_cleaner_task, _health_engine
    if _pool_cleaner_task:
        _pool_cleaner_task.cancel()
        _pool_cleaner_task = None
    if _health_engine:
        await _health_engine.dispose()
        _health_engine = None
    engine = get_engine()
    await engine.dispose()
    logger.info("db_engine_disposed")
