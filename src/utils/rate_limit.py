"""Karsa Trading System - Redis Rate Limiting (Token Bucket)"""

import asyncio
import time

import redis.asyncio as redis

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("rate_limit")


class RateLimiter:
    """Token bucket rate limiter using Redis."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def check_rate_limit(
        self,
        key: str,
        max_tokens: int,
        refill_rate: float,
        refill_interval: float = 1.0,
    ) -> bool:
        """
        Check if action is allowed under rate limit.

        Args:
            key: Rate limit key (e.g., 'agent:idx_analyst')
            max_tokens: Maximum tokens in bucket
            refill_rate: Tokens added per refill_interval
            refill_interval: Seconds between refills

        Returns:
            True if action is allowed, False if rate limited
        """
        full_key = f"{settings.redis_rate_limit_key}:{key}"
        now = time.time()

        lua_script = """
        local key = KEYS[1]
        local max_tokens = tonumber(ARGV[1])
        local refill_rate = tonumber(ARGV[2])
        local refill_interval = tonumber(ARGV[3])
        local now = tonumber(ARGV[4])
        local requested = tonumber(ARGV[5])

        local data = redis.call('HMGET', key, 'tokens', 'last_refill')
        local tokens = tonumber(data[1]) or max_tokens
        local last_refill = tonumber(data[2]) or now

        local elapsed = now - last_refill
        local refill_count = math.floor(elapsed / refill_interval)
        tokens = math.min(max_tokens, tokens + (refill_count * refill_rate))
        last_refill = last_refill + (refill_count * refill_interval)

        if tokens >= requested then
            tokens = tokens - requested
            redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
            redis.call('EXPIRE', key, 3600)
            return 1
        else
            redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
            redis.call('EXPIRE', key, 3600)
            return 0
        end
        """

        try:
            result = await self.redis.eval(
                lua_script,
                1,
                full_key,
                str(max_tokens),
                str(refill_rate),
                str(refill_interval),
                str(now),
                "1",
            )
            return bool(result)
        except Exception as e:
            logger.error("rate_limit_check_failed", key=key, error=str(e))
            return False  # Fail closed

    async def wait_for_token(
        self,
        key: str,
        max_tokens: int,
        refill_rate: float,
        wait_seconds: float = 5.0,
    ) -> bool:
        """Wait for a token to become available."""
        start = time.time()
        while time.time() - start < wait_seconds:
            if await self.check_rate_limit(key, max_tokens, refill_rate):
                return True
            await asyncio.sleep(0.5)
        return False
