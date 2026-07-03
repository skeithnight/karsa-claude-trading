"""Karsa Trading System — Distributed Execution Lock

Prevents double-execution between ASM and standard Orchestrator.
Uses Redis SET NX EX — one line of real logic.
"""

import redis.asyncio as redis

from src.utils.logging import get_logger

logger = get_logger("distributed_lock")

LOCK_TTL_SEC = 60  # ponytail: lock auto-expires even if holder crashes


async def acquire_execution_lock(r: redis.Redis, symbol: str, ttl: int = LOCK_TTL_SEC) -> bool:
    """Try to acquire execution lock for a symbol. Returns True if acquired."""
    key = f"karsa:lock:exec:{symbol}"
    try:
        acquired = await r.set(key, "1", nx=True, ex=ttl)
        if acquired:
            logger.debug("lock_acquired", symbol=symbol)
        else:
            logger.info("lock_contention", symbol=symbol)
        return bool(acquired)
    except Exception as e:
        logger.error("lock_acquire_failed", symbol=symbol, error=str(e))
        return False  # Fail closed — don't trade if lock system is broken


async def release_execution_lock(r: redis.Redis, symbol: str):
    """Release execution lock for a symbol."""
    key = f"karsa:lock:exec:{symbol}"
    try:
        await r.delete(key)
        logger.debug("lock_released", symbol=symbol)
    except Exception as e:
        logger.warning("lock_release_failed", symbol=symbol, error=str(e))
