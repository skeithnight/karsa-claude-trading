"""Tests for CircuitBreakerManager — daily DD, volatility spike, correlation cascade."""

import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

from src.risk.circuit_breaker import (
    CircuitBreakerManager,
    CB_KEY_PREFIX,
    CB_TTL_SEC,
    VOL_SPIKE_PCT,
    VOL_SPIKE_LOOKBACK,
    CORRELATION_CASCADE_PCT,
)


@pytest.fixture
def bybit():
    client = AsyncMock()
    client._http_client = AsyncMock()
    return client


@pytest.fixture
def redis_client():
    r = AsyncMock()
    r.get.return_value = None
    return r


@pytest.fixture
def cb_mgr(bybit, redis_client):
    return CircuitBreakerManager(redis_client, bybit)


# ── Constants ──────────────────────────────────────────────────────────────────

class TestConstants:
    def test_cb_key_prefix(self):
        assert CB_KEY_PREFIX == "karsa:circuit_breaker"

    def test_cb_ttl(self):
        assert CB_TTL_SEC == 1800  # 30 min

    def test_vol_spike_threshold(self):
        assert VOL_SPIKE_PCT == 5.0

    def test_vol_spike_lookback(self):
        assert VOL_SPIKE_LOOKBACK == 15  # minutes

    def test_correlation_cascade_pct(self):
        assert CORRELATION_CASCADE_PCT == 0.6


# ── Breaker Active Check ───────────────────────────────────────────────────────

class TestBreakerActive:
    @pytest.mark.asyncio
    async def test_no_redis_returns_false(self, cb_mgr):
        cb_mgr._redis = None
        assert await cb_mgr._is_breaker_active("DAILY_DD") is False

    @pytest.mark.asyncio
    async def test_key_missing_returns_false(self, cb_mgr, redis_client):
        redis_client.get.return_value = None
        assert await cb_mgr._is_breaker_active("DAILY_DD") is False

    @pytest.mark.asyncio
    async def test_key_exists_returns_true(self, cb_mgr, redis_client):
        redis_client.get.return_value = b'{"daily_pnl_pct": -3.5}'
        assert await cb_mgr._is_breaker_active("DAILY_DD") is True


# ── Daily Drawdown ─────────────────────────────────────────────────────────────

class TestDailyDrawdown:
    @pytest.mark.asyncio
    async def test_under_limit_no_trigger(self, cb_mgr):
        """No trigger when daily loss is under limit."""
        with patch('src.risk.circuit_breaker.async_session') as mock_session, \
             patch('src.config.settings') as mock_settings:
            mock_settings.CRYPTO_DAILY_LOSS_LIMIT_PCT = 3.0
            mock_session.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await cb_mgr.check_daily_drawdown()
        assert result is None


# ── Volatility Spike ───────────────────────────────────────────────────────────

class TestVolatilitySpike:
    @pytest.mark.asyncio
    async def test_no_spike_under_threshold(self, cb_mgr, bybit, redis_client):
        """No trigger when move < 5%."""
        redis_client.get.return_value = None
        # Klines with small range: high=65200, low=64800 → ~0.62% move
        bybit._http_client.get_kline.return_value = {
            "retCode": 0,
            "result": {"list": [
                ["1000", "65000", "65200", "64800", "65100", "100"],
                ["1000", "65100", "65150", "64900", "65050", "80"],
            ]},
        }
        result = await cb_mgr.check_volatility_spike("BTCUSDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_spike_above_threshold(self, cb_mgr, bybit, redis_client):
        """Trigger when move >= 5%."""
        redis_client.get.return_value = None
        # Klines with big range: high=70000, low=65000 → 7.69% move
        bybit._http_client.get_kline.return_value = {
            "retCode": 0,
            "result": {"list": [
                ["1000", "65000", "70000", "65000", "68000", "500"],
                ["1000", "68000", "69000", "66000", "67000", "300"],
            ]},
        }
        result = await cb_mgr.check_volatility_spike("BTCUSDT")
        assert result is not None
        assert result["breaker"] == "VOLATILITY"
        assert result["severity"] == "WARNING"
        assert result["ticker"] == "BTCUSDT"
        assert result["move_pct"] >= 5.0

    @pytest.mark.asyncio
    async def test_already_active_skips(self, cb_mgr, bybit, redis_client):
        """Skip check if breaker already active."""
        redis_client.get.return_value = b'{"move_pct": 7.5}'
        result = await cb_mgr.check_volatility_spike("BTCUSDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self, cb_mgr, bybit, redis_client):
        """Gracefully handle API errors."""
        redis_client.get.return_value = None
        bybit._http_client.get_kline.return_value = {"retCode": 10001, "result": {"list": []}}
        result = await cb_mgr.check_volatility_spike("BTCUSDT")
        assert result is None


# ── Correlation Cascade ────────────────────────────────────────────────────────

class TestCorrelationCascade:
    @pytest.mark.asyncio
    async def test_no_cascade_under_threshold(self, cb_mgr, bybit, redis_client):
        """No trigger when <60% of tier is losing."""
        redis_client.get.return_value = None
        bybit._http_client.get_positions.return_value = {
            "retCode": 0,
            "result": {"list": [
                {"symbol": "BTCUSDT", "size": "0.01", "unrealisedPnl": "500"},
                {"symbol": "ETHUSDT", "size": "0.1", "unrealisedPnl": "-100"},
            ]},
        }
        result = await cb_mgr.check_correlation_cascade()
        # 1/2 = 50% losing < 60% threshold
        assert result is None

    @pytest.mark.asyncio
    async def test_cascade_above_threshold(self, cb_mgr, bybit, redis_client):
        """Trigger when >=60% of tier is losing."""
        redis_client.get.return_value = None
        bybit._http_client.get_positions.return_value = {
            "retCode": 0,
            "result": {"list": [
                {"symbol": "BTCUSDT", "size": "0.01", "unrealisedPnl": "-200"},
                {"symbol": "ETHUSDT", "size": "0.1", "unrealisedPnl": "-100"},
            ]},
        }
        result = await cb_mgr.check_correlation_cascade()
        # 2/2 = 100% losing >= 60% threshold
        assert result is not None
        assert result["breaker"] == "CORRELATION"
        assert result["severity"] == "WARNING"
        assert result["loss_ratio"] >= 0.6

    @pytest.mark.asyncio
    async def test_single_position_no_cascade(self, cb_mgr, bybit, redis_client):
        """No cascade with single position in tier (need >=2)."""
        redis_client.get.return_value = None
        bybit._http_client.get_positions.return_value = {
            "retCode": 0,
            "result": {"list": [
                {"symbol": "BTCUSDT", "size": "0.01", "unrealisedPnl": "-500"},
            ]},
        }
        result = await cb_mgr.check_correlation_cascade()
        assert result is None


# ── Activate Breaker ───────────────────────────────────────────────────────────

class TestActivateBreaker:
    @pytest.mark.asyncio
    async def test_sets_redis_key(self, cb_mgr, redis_client):
        await cb_mgr._activate_breaker("VOLATILITY:BTCUSDT", "WARNING", {"move_pct": 7.5})
        redis_client.setex.assert_called_once()
        args = redis_client.setex.call_args
        assert args[0][0] == "karsa:circuit_breaker:VOLATILITY:BTCUSDT"
        assert args[0][1] == CB_TTL_SEC

    @pytest.mark.asyncio
    async def test_no_redis_skips(self, cb_mgr):
        cb_mgr._redis = None
        # Should not raise
        await cb_mgr._activate_breaker("DAILY_DD", "HALT", {})


# ── Get Active Breakers ────────────────────────────────────────────────────────

class TestGetActiveBreakers:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_redis(self, cb_mgr):
        cb_mgr._redis = None
        result = await cb_mgr.get_active_breakers()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_active_breakers(self, cb_mgr, redis_client):
        async def fake_scan(match):
            for k in ["karsa:circuit_breaker:DAILY_DD", "karsa:circuit_breaker:VOLATILITY:BTCUSDT"]:
                yield k
        redis_client.scan_iter = fake_scan
        redis_client.get.return_value = b'{"test": true}'
        redis_client.ttl.return_value = 900
        result = await cb_mgr.get_active_breakers()
        assert len(result) == 2
        assert all("type" in b for b in result)
        assert all("ttl" in b for b in result)
