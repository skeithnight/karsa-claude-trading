"""Tests for CircuitBreakerManager — daily DD, volatility spike, correlation cascade."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
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
    client._http_client = MagicMock()
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


# ── Daily DD Edge Cases ─────────────────────────────────────────────────────

class TestDailyDrawdownEdgeCases:
    @pytest.mark.asyncio
    async def test_exactly_at_limit_triggers(self, cb_mgr, redis_client):
        """When daily_pnl_pct is exactly at -limit, should trigger."""
        redis_client.get.return_value = None  # breaker not already active

        with patch('src.risk.circuit_breaker.async_session') as mock_session_cls:
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar.return_value = -3.0
            mock_session.execute = AsyncMock(return_value=mock_result)

            with patch('src.config.settings') as mock_settings:
                mock_settings.CRYPTO_DAILY_LOSS_LIMIT_PCT = 3.0
                with patch.object(cb_mgr, '_activate_breaker', new_callable=AsyncMock) as mock_activate:
                    result = await cb_mgr.check_daily_drawdown()

        assert result is not None
        assert result["breaker"] == "DAILY_DD"

    @pytest.mark.asyncio
    async def test_already_active_returns_none(self, cb_mgr, redis_client):
        """When breaker is already active, should return None (skip re-activation)."""
        redis_client.get.return_value = b'{"active": true}'  # already active

        with patch('src.risk.circuit_breaker.async_session') as mock_session_cls:
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar.return_value = -5.0
            mock_session.execute = AsyncMock(return_value=mock_result)

            with patch('src.config.settings') as mock_settings:
                mock_settings.CRYPTO_DAILY_LOSS_LIMIT_PCT = 3.0
                result = await cb_mgr.check_daily_drawdown()

        assert result is None

    @pytest.mark.asyncio
    async def test_db_exception_returns_none(self, cb_mgr, redis_client):
        """When async_session raises, should return None gracefully."""
        redis_client.get.return_value = None

        with patch('src.risk.circuit_breaker.async_session', side_effect=Exception("DB down")):
            result = await cb_mgr.check_daily_drawdown()

        assert result is None


# ── Volatility Spike Edge Cases ─────────────────────────────────────────────

class TestVolatilitySpikeEdgeCases:
    @pytest.mark.asyncio
    async def test_insufficient_klines_returns_none(self, cb_mgr, redis_client):
        """When kline list has < 2 entries, returns None."""
        redis_client.get.return_value = None
        cb_mgr.bybit._http_client.get_kline = MagicMock(return_value={
            "retCode": 0,
            "result": {"list": [["1000", "50000", "50100", "49900", "50050", "100"]]},
        })
        result = await cb_mgr.check_volatility_spike("BTCUSDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_klines_returns_none(self, cb_mgr, redis_client):
        """When kline list is empty, returns None."""
        redis_client.get.return_value = None
        cb_mgr.bybit._http_client.get_kline = MagicMock(return_value={
            "retCode": 0,
            "result": {"list": []},
        })
        result = await cb_mgr.check_volatility_spike("BTCUSDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_min_low_zero_skips(self, cb_mgr, redis_client):
        """When klines have min_low of 0, should skip to avoid division by zero."""
        redis_client.get.return_value = None
        cb_mgr.bybit._http_client.get_kline = MagicMock(return_value={
            "retCode": 0,
            "result": {"list": [
                ["1000", "50000", "50100", "0", "50050", "100"],
                ["1001", "50000", "50100", "0", "50050", "100"],
            ]},
        })
        result = await cb_mgr.check_volatility_spike("BTCUSDT")
        assert result is None


# ── Correlation Cascade Edge Cases ──────────────────────────────────────────

class TestCorrelationCascadeEdgeCases:
    @pytest.mark.asyncio
    async def test_already_active_returns_none(self, cb_mgr, redis_client):
        """When CORRELATION breaker already active, returns None."""
        redis_client.get.return_value = b'{"active": true}'
        result = await cb_mgr.check_correlation_cascade()
        assert result is None

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self, cb_mgr, redis_client):
        """When get_positions returns error, returns None."""
        redis_client.get.return_value = None
        cb_mgr.bybit._http_client.get_positions = MagicMock(return_value={
            "retCode": 10001, "result": {"list": []},
        })
        result = await cb_mgr.check_correlation_cascade()
        assert result is None

    @pytest.mark.asyncio
    async def test_tier2_cascade(self, cb_mgr, redis_client):
        """Test cascade with tier2 positions (>60% losing)."""
        redis_client.get.return_value = None
        cb_mgr.bybit._http_client.get_positions = MagicMock(return_value={
            "retCode": 0,
            "result": {"list": [
                {"symbol": "SOLUSDT", "size": "10", "unrealisedPnl": "-100"},
                {"symbol": "AVAXUSDT", "size": "50", "unrealisedPnl": "-50"},
                {"symbol": "LINKUSDT", "size": "20", "unrealisedPnl": "10"},
            ]},
        })

        with patch.object(cb_mgr, '_activate_breaker', new_callable=AsyncMock):
            result = await cb_mgr.check_correlation_cascade()

        assert result is not None
        assert result["breaker"] == "CORRELATION"
        assert result["tier"] == "tier2"
        assert result["loss_ratio"] >= CORRELATION_CASCADE_PCT


# ── check_all ───────────────────────────────────────────────────────────────

class TestCheckAll:
    @pytest.mark.asyncio
    async def test_combines_all_events(self, cb_mgr):
        """check_all returns combined list from all breakers."""
        dd_event = {"breaker": "DAILY_DD", "severity": "HALT"}
        vol_event = {"breaker": "VOLATILITY", "severity": "WARNING", "ticker": "BTCUSDT"}
        corr_event = {"breaker": "CORRELATION", "severity": "WARNING", "tier": "tier1"}

        with patch.object(cb_mgr, 'check_daily_drawdown', new_callable=AsyncMock, return_value=dd_event), \
             patch.object(cb_mgr, 'check_volatility_spike', new_callable=AsyncMock, return_value=vol_event), \
             patch.object(cb_mgr, 'check_correlation_cascade', new_callable=AsyncMock, return_value=corr_event):
            events = await cb_mgr.check_all()

        # 1 DD + 3 vol (BTC, ETH, SOL) + 1 corr = 5
        assert len(events) == 5
        assert events[0] == dd_event

    @pytest.mark.asyncio
    async def test_no_events_returns_empty(self, cb_mgr):
        """When nothing triggers, returns empty list."""
        with patch.object(cb_mgr, 'check_daily_drawdown', new_callable=AsyncMock, return_value=None), \
             patch.object(cb_mgr, 'check_volatility_spike', new_callable=AsyncMock, return_value=None), \
             patch.object(cb_mgr, 'check_correlation_cascade', new_callable=AsyncMock, return_value=None):
            events = await cb_mgr.check_all()

        assert events == []


# ── _is_breaker_active Edge Cases ───────────────────────────────────────────

class TestBreakerActiveEdge:
    @pytest.mark.asyncio
    async def test_redis_exception_returns_false(self, cb_mgr, redis_client):
        """When Redis.get raises, returns False (fail-open)."""
        redis_client.get.side_effect = Exception("Redis down")
        result = await cb_mgr._is_breaker_active("DAILY_DD")
        assert result is False


# ── _activate_breaker Edge Cases ────────────────────────────────────────────

class TestActivateBreakerEdge:
    @pytest.mark.asyncio
    async def test_db_exception_does_not_propagate(self, cb_mgr, redis_client):
        """When DB insert raises, should not propagate exception."""
        redis_client.setex = AsyncMock()

        with patch('src.risk.circuit_breaker.async_session') as mock_session_cls:
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.add = MagicMock(side_effect=Exception("DB write failed"))
            mock_session.commit = AsyncMock(side_effect=Exception("DB write failed"))

            # Should not raise
            await cb_mgr._activate_breaker("DAILY_DD", "HALT", {"test": True})

    @pytest.mark.asyncio
    async def test_redis_exception_does_not_propagate(self, cb_mgr, redis_client):
        """When Redis setex raises, should not propagate."""
        redis_client.setex = AsyncMock(side_effect=Exception("Redis down"))

        with patch('src.risk.circuit_breaker.async_session') as mock_session_cls:
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should not raise
            await cb_mgr._activate_breaker("DAILY_DD", "HALT", {"test": True})


# ── get_active_breakers Edge Cases ──────────────────────────────────────────

class TestGetActiveBreakersEdge:
    @pytest.mark.asyncio
    async def test_scan_exception_returns_empty(self, cb_mgr, redis_client):
        """When scan_iter raises, returns empty list."""
        async def failing_scan(match):
            raise Exception("Redis scan failed")
            yield  # make it an async generator

        redis_client.scan_iter = failing_scan
        result = await cb_mgr.get_active_breakers()
        assert result == []
