"""Emergency stop / kill switch — Redis-backed, survives orchestrator restarts."""

import json
from datetime import datetime, timezone

import redis.asyncio as aioredis

from src.config import settings
from src.metrics.crypto_metrics import update_kill_switch

KILL_KEY = f"{settings.REDIS_PREFIX}:emergency_stop"
GLOBAL_HALT_KEY = f"{settings.REDIS_PREFIX}:global_halt"

_client: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


async def activate(reason: str, operator: str) -> bool:
    """Activate emergency stop — halts all new trading decisions.

    Uses SET NX for atomicity — returns True if this call activated,
    False if already active (prevents duplicate alerts).
    """
    payload = json.dumps({
        "active": True,
        "reason": reason,
        "operator": operator,
        "activated_at": datetime.now(timezone.utc).isoformat(),
    })
    result = await _get_redis().set(KILL_KEY, payload, nx=True)
    if result:
        update_kill_switch(True)
    return bool(result)  # True if set, False if already existed


async def deactivate(operator: str) -> None:
    """Deactivate emergency stop — resume trading."""
    await _get_redis().delete(KILL_KEY)
    update_kill_switch(False)


async def is_active() -> bool:
    """Check if emergency stop is active."""
    val = await _get_redis().get(KILL_KEY)
    if val:
        return json.loads(val).get("active", False)
    return False


async def get_status() -> dict | None:
    """Get full emergency stop status payload."""
    val = await _get_redis().get(KILL_KEY)
    return json.loads(val) if val else None


# --- Global Halt (OOB Kill Switch for Crypto) ---

async def activate_global_halt(reason: str, operator: str) -> bool:
    """Activate global halt — bypasses message queues, forces all agents to stop.

    Sets both the global halt key AND the standard emergency stop for compatibility.
    Used by /kill command on crypto bot.
    """
    payload = json.dumps({
        "active": True,
        "reason": reason,
        "operator": operator,
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "type": "global_halt",
    })
    r = _get_redis()
    result = await r.set(GLOBAL_HALT_KEY, payload, nx=True)
    # Also set standard emergency stop
    await r.set(KILL_KEY, payload)
    update_kill_switch(True)
    return bool(result)


async def deactivate_global_halt(operator: str) -> None:
    """Deactivate global halt — resume all trading."""
    r = _get_redis()
    await r.delete(GLOBAL_HALT_KEY)
    await r.delete(KILL_KEY)
    update_kill_switch(False)


async def is_global_halt() -> bool:
    """Check if global halt is active."""
    val = await _get_redis().get(GLOBAL_HALT_KEY)
    return bool(val)
