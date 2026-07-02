"""Tests for TrailingStopManager — ATR-based trailing with regime multipliers."""

import pytest
from unittest.mock import AsyncMock, patch
from decimal import Decimal

from src.risk.trailing_stop import (
    TrailingStopManager,
    REGIME_TRAIL_MULTIPLIER,
    BREAKEVEN_ATR_FRACTION,
    COOLDOWN_SEC,
    MIN_TRAIL_CHANGE_ATR,
)


@pytest.fixture
def bybit():
    client = AsyncMock()
    client._http_client = AsyncMock()
    return client


@pytest.fixture
def redis_client():
    r = AsyncMock()
    r.get.return_value = None  # no cooldown by default
    return r


@pytest.fixture
def trail_mgr(bybit, redis_client):
    return TrailingStopManager(bybit, redis_client)


# ── Constants ──────────────────────────────────────────────────────────────────

class TestConstants:
    def test_regime_multipliers(self):
        assert REGIME_TRAIL_MULTIPLIER["TREND_BULL"] == 2.0
        assert REGIME_TRAIL_MULTIPLIER["MEAN_REVERSION"] == 1.5
        assert REGIME_TRAIL_MULTIPLIER["CHOP"] == 0

    def test_breakeven_atr_fraction(self):
        assert BREAKEVEN_ATR_FRACTION == Decimal("0.10")

    def test_cooldown_seconds(self):
        assert COOLDOWN_SEC == 300

    def test_min_trail_change(self):
        assert MIN_TRAIL_CHANGE_ATR == 0.05


# ── Cooldown ───────────────────────────────────────────────────────────────────

class TestCooldown:
    @pytest.mark.asyncio
    async def test_no_cooldown_when_no_redis(self, trail_mgr):
        trail_mgr._redis = None
        assert await trail_mgr._check_cooldown("BTCUSDT") is False

    @pytest.mark.asyncio
    async def test_no_cooldown_when_key_missing(self, trail_mgr, redis_client):
        redis_client.get.return_value = None
        assert await trail_mgr._check_cooldown("BTCUSDT") is False

    @pytest.mark.asyncio
    async def test_cooldown_active(self, trail_mgr, redis_client):
        redis_client.get.return_value = b"1"
        assert await trail_mgr._check_cooldown("BTCUSDT") is True

    @pytest.mark.asyncio
    async def test_set_cooldown(self, trail_mgr, redis_client):
        await trail_mgr._set_cooldown("BTCUSDT")
        redis_client.setex.assert_called_once_with(
            "karsa:trail_cooldown:BTCUSDT", COOLDOWN_SEC, "1"
        )


# ── Update Trailing Stops ──────────────────────────────────────────────────────

class TestUpdateTrailingStops:
    @pytest.mark.asyncio
    async def test_empty_positions(self, trail_mgr):
        result = await trail_mgr.update_trailing_stops([])
        assert result == []

    @pytest.mark.asyncio
    async def test_skip_chop_regime(self, trail_mgr, redis_client):
        """CHOP regime should skip trailing (multiplier=0)."""
        positions = [{
            "id": 1,
            "ticker": "BTCUSDT",
            "entry_price": Decimal("65000"),
            "highest_price": Decimal("68000"),
            "trailing_stop_price": None,
            "direction": "LONG",
            "signal_source": "trend",
        }]
        result = await trail_mgr.update_trailing_stops(positions, regime="CHOP")
        assert result == []

    @pytest.mark.asyncio
    async def test_no_new_high_no_stop_update(self, trail_mgr, redis_client):
        """If price didn't make new high, stop shouldn't change."""
        positions = [{
            "id": 1,
            "ticker": "BTCUSDT",
            "entry_price": Decimal("65000"),
            "highest_price": Decimal("68000"),
            "trailing_stop_price": Decimal("64000"),
            "direction": "LONG",
            "signal_source": "trend",
        }]
        # Mock get_ticker returning price below highest
        with patch.object(trail_mgr, '_get_current_price', return_value=Decimal("67500")):
            result = await trail_mgr.update_trailing_stops(positions, regime="TREND_BULL")
        assert result == []

    @pytest.mark.asyncio
    async def test_breakeven_floor(self, trail_mgr, redis_client):
        """Stop should never go below entry + 10% of ATR."""
        positions = [{
            "id": 1,
            "ticker": "BTCUSDT",
            "entry_price": Decimal("65000"),
            "highest_price": Decimal("65100"),
            "trailing_stop_price": None,
            "direction": "LONG",
            "signal_source": "trend",
        }]
        with patch.object(trail_mgr, '_get_current_price', return_value=Decimal("65200")), \
             patch.object(trail_mgr, '_get_ohlcv', return_value=[
                 {"high": 65200, "low": 64800, "close": 65100},
                 {"high": 65300, "low": 64900, "close": 65050},
                 {"high": 65100, "low": 64700, "close": 64900},
             ]), \
             patch('src.risk.trailing_stop.calculate_atr', return_value={"atr": 200.0}):
            result = await trail_mgr.update_trailing_stops(positions, regime="TREND_BULL")

        if result:
            # Stop should be at least entry + 10% of ATR
            min_stop = Decimal("65000") + Decimal("200.0") * Decimal("0.10")
            assert result[0]["new_stop"] >= min_stop
