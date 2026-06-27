"""Emergency stop / kill switch — Redis-backed, survives orchestrator restarts."""

import json
from datetime import datetime, timezone

import redis.asyncio as aioredis

from src.config import settings

KILL_KEY = f"{settings.REDIS_PREFIX}:emergency_stop"

_client: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


async def activate(reason: str, operator: str) -> None:
    """Activate emergency stop — halts all new trading decisions."""
    payload = json.dumps({
        "active": True,
        "reason": reason,
        "operator": operator,
        "activated_at": datetime.now(timezone.utc).isoformat(),
    })
    await _get_redis().set(KILL_KEY, payload)


async def deactivate(operator: str) -> None:
    """Deactivate emergency stop — resume trading."""
    await _get_redis().delete(KILL_KEY)


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
