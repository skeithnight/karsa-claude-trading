"""Karsa Trading System - Database Connection Management

Uses lazy engine creation to ensure the asyncpg pool is bound to the correct
event loop (the one created by asyncio.run()). Includes pool recycling and
periodic cleanup to prevent connection leaks from event loop mismatches.

ROOT CAUSE FIX: Monkey-patches the asyncpg connection's terminate() method
to always use _terminate_force_close() instead of the broken graceful close
path. The graceful close uses asyncio.shield() which creates a Future on
one event loop but the coroutine runs on another — causing the
"Future attached to a different loop" error and connection leaks.

PREVENTION (v2):
- Single authoritative pool_reset() under lock — every dispose path calls it.
- get_engine() raises on None instead of silently creating a racing fallback.
- pool_timeout raised to 30s; statement_timeout=25s via connect_args.
- _pool_recycle_loop interval halved to 60s; kills idle-in-transaction > 20s.
- Dispose cooldown: 45s minimum between consecutive disposes.
"""

import asyncio
import logging
import time
import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase

from src.config import settings

logger = logging.getLogger("database")

def _patch_asyncpg_terminate():
    """Monkey-patch asyncpg connection close/terminate to avoid event loop thread check.

    The bug: Both close() and terminate() eventually call protocol methods that use
    loop.call_soon() — triggering a thread check that fails cross-event-loop.

    Fix: Patch _abort() to skip protocol.abort(), and patch close() to use _abort()
    instead of _protocol.close(). Both avoid the broken event loop path.
    """
    try:
        # Patch raw asyncpg Connection._abort to avoid event loop thread check
        def _safe_abort(self):
            self._aborted = True
            # Skip self._protocol.abort() — it calls loop.call_soon() which
            # fails when called from a different event loop/thread.
            # Force-close the TCP transport so the Postgres-side connection
            # is actually dropped (otherwise it lingers as "idle" in
            # pg_stat_activity, inflating the idle connection count).
            if hasattr(self, '_transport') and self._transport is not None:
                try:
                    self._transport.close()
                except Exception:
                    pass
            self._protocol = None

        asyncpg.Connection._abort = _safe_abort

        # Patch close() to use _abort() instead of _protocol.close()
        async def _safe_close(self, *, timeout=None):
            if not self.is_closed():
                self._abort()
            self._cleanup()

        asyncpg.Connection.close = _safe_close

        # Patch SQLAlchemy's wrapper to use terminate() (which calls _abort)
        from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_connection

        def _fixed_terminate(self):
            if self._connection and not self._connection.is_closed():
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

# Pool configuration constants — single source of truth
_POOL_SIZE = 20      # Raised from 10 — 20+ concurrent scheduler jobs need headroom
_MAX_OVERFLOW = 10   # Raised from 5 — total max = 30 connections
_POOL_TIMEOUT = 30       # seconds to wait for a slot; raised from 10
_POOL_RECYCLE = 1800     # recycle connections every 30 min
# asyncpg-level statement timeout prevents single query from holding slot forever
_STATEMENT_TIMEOUT_MS = 25_000   # 25 s

# --- Lazy engine / session factory ------------------------------------------
_engine = None
_session_factory = None
_pool_cleaner_task = None
_health_engine = None          # NullPool engine — never competes with main pool
_engine_lock = None            # Created lazily on the running event loop
_last_dispose_time: float = 0.0   # Cooldown guard: minimum 45s between disposes

def _get_lock():
    """Get or create the engine lock on the current event loop."""
    global _engine_lock
    if _engine_lock is None:
        _engine_lock = asyncio.Lock()
    return _engine_lock

def _make_engine():
    """Create a new async engine with standard config."""
    return create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_size=_POOL_SIZE,
        max_overflow=_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_timeout=_POOL_TIMEOUT,
        pool_recycle=_POOL_RECYCLE,
        # Pass statement_timeout to asyncpg so no single query holds a
        # connection slot longer than _STATEMENT_TIMEOUT_MS.
        connect_args={"server_settings": {"statement_timeout": str(_STATEMENT_TIMEOUT_MS)}},
    )

async def pool_reset(reason: str = "manual") -> bool:
    """Dispose the current engine and reset globals — the ONLY authorised
    dispose path.

    All callers (watchdog, health endpoint, recycle loop) must call this
    instead of directly touching _engine / _session_factory.  Holds
    _get_lock() so concurrent calls serialise safely.  Enforces a 45-second
    cooldown to prevent dispose storms.

    Returns True if a dispose was performed, False if cooldown skipped it.
    """
    global _engine, _session_factory, _last_dispose_time

    now = time.monotonic()
    cooldown = 45.0
    if now - _last_dispose_time < cooldown:
        remaining = cooldown - (now - _last_dispose_time)
        logger.debug(
            "pool_reset_cooldown_skipped reason=%s remaining=%.1fs", reason, remaining
        )
        return False

    async with _get_lock():
        # Re-check cooldown inside the lock (another coroutine may have just reset)
        now = time.monotonic()
        if now - _last_dispose_time < cooldown:
            return False

        engine_to_dispose = _engine
        _engine = None
        _session_factory = None
        _last_dispose_time = now

        # Dispose INSIDE the lock to prevent race condition where a new engine
        # is created while the old one is still disposing (causes shared asyncpg
        # pool connections to be terminated — InterfaceError: connection is closed)
        if engine_to_dispose is not None:
            try:
                await engine_to_dispose.dispose()
                logger.warning("pool_reset_disposed reason=%s", reason)
            except Exception as e:
                logger.warning("pool_reset_dispose_error reason=%s error=%s", reason, str(e))

        _engine = _make_engine()
        logger.info("db_engine_recreated pool_size=%d max_overflow=%d", _POOL_SIZE, _MAX_OVERFLOW)

    return True

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
    If either exceeds the limit, calls pool_reset() to flush leaked connections.
    """
    while True:
        await asyncio.sleep(60)   # every 60s (was 120s)
        try:
            try:
                engine = get_engine()
            except RuntimeError:
                # Engine is None during an active pool_reset — skip this cycle
                continue
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
                    expected_max = _POOL_SIZE + _MAX_OVERFLOW
                    leak_threshold = expected_max + 3  # small margin

                    logger.debug(
                        "pool_status idle=%d expected_max=%d checked_in=%d checked_out=%d overflow=%d",
                        idle_conns, expected_max, pool.checkedin(), pool.checkedout(), pool.overflow(),
                    )

                    # Terminate stuck "idle in transaction" connections directly.
                    # Threshold tightened from 30s → 20s to catch leaks earlier.
                    try:
                        stuck_result = await conn.execute(
                            text(
                                "SELECT pg_terminate_backend(pid) "
                                "FROM pg_stat_activity "
                                "WHERE datname = current_database() "
                                "AND state = 'idle in transaction' "
                                "AND query_start < NOW() - INTERVAL '20 seconds'"
                            )
                        )
                        killed = sum(1 for row in stuck_result if row[0])
                        if killed > 0:
                            logger.warning("pg_killed_stale_transactions count=%d", killed)
                    except Exception:
                        pass

                    # Trigger pool reset via the single authorised path
                    if idle_conns > leak_threshold:
                        logger.warning(
                            "pg_idle_connections_high count=%d expected_max=%d — resetting pool",
                            idle_conns, expected_max,
                        )
                        await pool_reset("idle_connections_high")
                    # ponytail: pool.overflow() < 0 is NORMAL (means fewer
                    # checked-out connections than pool_size).  Removed the
                    # false-positive reset that was destroying the engine
                    # every 60s and cascading errors.

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
    async with _get_lock():
        if _engine is None:
            _engine = _make_engine()
            logger.info(
                "db_engine_created pool_size=%d max_overflow=%d pool_timeout=%d",
                _POOL_SIZE, _MAX_OVERFLOW, _POOL_TIMEOUT,
            )
    return _engine

def get_engine():
    """Return the shared async engine (sync accessor — engine must exist).

    Call init_db() at startup to guarantee the engine is created.
    After that, get_engine() is safe to call from any coroutine.

    PREVENTION: Unlike the old fallback, this now raises RuntimeError if the
    engine is None instead of silently creating an engine outside the lock
    (which caused a race where two engines were born simultaneously and one
    was orphaned, leaking all its connections permanently).
    """
    global _engine
    if _engine is None:
        raise RuntimeError(
            "DB engine not initialised — call await init_db() at startup first."
        )
    return _engine

def get_session():
    """Return a new async session (context-manager ready).

    Caches the session factory to avoid creating thousands of factory objects
    per scheduler cycle. The factory is reset in pool_reset() so after a
    dispose the next session uses the fresh engine.
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory()

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
    global _pool_cleaner_task, _health_engine, _engine
    if _pool_cleaner_task:
        _pool_cleaner_task.cancel()
        _pool_cleaner_task = None
    if _health_engine:
        await _health_engine.dispose()
        _health_engine = None
    if _engine:
        await _engine.dispose()
        _engine = None
    logger.info("db_engine_disposed")
