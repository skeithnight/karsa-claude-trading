"""Tests for src/utils/rate_limit.py — RateLimiter with Redis Lua script."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.rate_limit import RateLimiter


@pytest.fixture
def mock_redis():
    """Create a mock Redis client with eval as AsyncMock."""
    redis = MagicMock()
    redis.eval = AsyncMock()
    return redis


@pytest.fixture
def limiter(mock_redis):
    return RateLimiter(mock_redis)


# ── check_rate_limit ───────────────────────────────────────────────

class TestCheckRateLimit:
    @pytest.mark.asyncio
    async def test_allowed_when_eval_returns_1(self, limiter, mock_redis):
        mock_redis.eval.return_value = 1
        result = await limiter.check_rate_limit("agent:test", max_tokens=10, refill_rate=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_denied_when_eval_returns_0(self, limiter, mock_redis):
        mock_redis.eval.return_value = 0
        result = await limiter.check_rate_limit("agent:test", max_tokens=10, refill_rate=1.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_fail_closed_on_exception(self, limiter, mock_redis):
        mock_redis.eval.side_effect = RuntimeError("connection refused")
        result = await limiter.check_rate_limit("agent:test", max_tokens=10, refill_rate=1.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_fail_closed_on_timeout(self, limiter, mock_redis):
        mock_redis.eval.side_effect = asyncio.TimeoutError()
        result = await limiter.check_rate_limit("agent:test", max_tokens=10, refill_rate=1.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_correct_key_format(self, limiter, mock_redis):
        """Full key should be {redis_rate_limit_key}:{user_key}"""
        mock_redis.eval.return_value = 1
        await limiter.check_rate_limit("agent:idx_analyst", max_tokens=5, refill_rate=2.0)

        # First positional arg to eval is the Lua script, second is numkeys,
        # third is the full key string
        call_args = mock_redis.eval.call_args
        full_key = call_args[0][2]  # script, numkeys, full_key, ...
        assert full_key.endswith(":agent:idx_analyst")
        assert "ratelimit" in full_key

    @pytest.mark.asyncio
    async def test_eval_called_with_correct_arg_count(self, limiter, mock_redis):
        """eval(script, numkeys=1, key, max_tokens, refill_rate, refill_interval, now, requested)"""
        mock_redis.eval.return_value = 1
        await limiter.check_rate_limit("test", max_tokens=10, refill_rate=1.0)
        call_args = mock_redis.eval.call_args[0]
        # script + numkeys + 6 string args = 8 positional args
        assert len(call_args) == 8

    @pytest.mark.asyncio
    async def test_max_tokens_passed_as_string(self, limiter, mock_redis):
        mock_redis.eval.return_value = 1
        await limiter.check_rate_limit("test", max_tokens=42, refill_rate=1.5)
        call_args = mock_redis.eval.call_args[0]
        assert call_args[3] == "42"      # max_tokens
        assert call_args[4] == "1.5"     # refill_rate

    @pytest.mark.asyncio
    async def test_refill_interval_default_1(self, limiter, mock_redis):
        mock_redis.eval.return_value = 1
        await limiter.check_rate_limit("test", max_tokens=10, refill_rate=1.0)
        call_args = mock_redis.eval.call_args[0]
        assert call_args[5] == "1.0"     # default refill_interval

    @pytest.mark.asyncio
    async def test_refill_interval_custom(self, limiter, mock_redis):
        mock_redis.eval.return_value = 1
        await limiter.check_rate_limit("test", max_tokens=10, refill_rate=1.0, refill_interval=5.0)
        call_args = mock_redis.eval.call_args[0]
        assert call_args[5] == "5.0"


# ── wait_for_token ─────────────────────────────────────────────────

class TestWaitForToken:
    @pytest.mark.asyncio
    async def test_immediate_success(self, limiter):
        with patch.object(limiter, "check_rate_limit", new_callable=AsyncMock, return_value=True):
            result = await limiter.wait_for_token("test", max_tokens=5, refill_rate=1.0, wait_seconds=2.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_retry_then_success(self, limiter):
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return call_count >= 3

        with patch.object(limiter, "check_rate_limit", new_callable=AsyncMock, side_effect=_side_effect):
            with patch("src.utils.rate_limit.asyncio.sleep", new_callable=AsyncMock):
                result = await limiter.wait_for_token("test", max_tokens=5, refill_rate=1.0, wait_seconds=5.0)

        assert result is True
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self, limiter):
        with patch.object(limiter, "check_rate_limit", new_callable=AsyncMock, return_value=False):
            with patch("src.utils.rate_limit.asyncio.sleep", new_callable=AsyncMock):
                result = await limiter.wait_for_token("test", max_tokens=5, refill_rate=1.0, wait_seconds=0.01)

        assert result is False

    @pytest.mark.asyncio
    async def test_sleep_called_between_retries(self, limiter):
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return call_count >= 2

        mock_sleep = AsyncMock()

        with patch.object(limiter, "check_rate_limit", new_callable=AsyncMock, side_effect=_side_effect):
            with patch("src.utils.rate_limit.asyncio.sleep", mock_sleep):
                await limiter.wait_for_token("test", max_tokens=5, refill_rate=1.0, wait_seconds=5.0)

        # sleep should have been called once (before the successful check)
        mock_sleep.assert_called_once_with(0.5)
