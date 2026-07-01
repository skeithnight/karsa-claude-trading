"""Tests for PositionManager — partial exits and time-based exits."""

import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from src.risk.position_manager import (
    PositionManager,
    PARTIAL_EXIT_TARGETS,
    TIME_EXIT_MAX_HOURS,
    TIME_EXIT_MIN_GAIN_PCT,
    COOLDOWN_SEC,
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
def pos_mgr(bybit, redis_client):
    return PositionManager(bybit, redis_client)


# ── Constants ──────────────────────────────────────────────────────────────────

class TestConstants:
    def test_partial_exit_targets(self):
        assert len(PARTIAL_EXIT_TARGETS) == 2
        assert PARTIAL_EXIT_TARGETS[0]["r_multiple"] == 1.0
        assert PARTIAL_EXIT_TARGETS[0]["exit_pct"] == 33
        assert PARTIAL_EXIT_TARGETS[1]["r_multiple"] == 2.0
        assert PARTIAL_EXIT_TARGETS[1]["exit_pct"] == 33

    def test_time_exit_max_hours(self):
        assert TIME_EXIT_MAX_HOURS == 72

    def test_time_exit_min_gain(self):
        assert TIME_EXIT_MIN_GAIN_PCT == Decimal("1.0")

    def test_cooldown_seconds(self):
        assert COOLDOWN_SEC == 120


# ── Cooldown ───────────────────────────────────────────────────────────────────

class TestCooldown:
    @pytest.mark.asyncio
    async def test_no_cooldown_when_no_redis(self, pos_mgr):
        pos_mgr._redis = None
        assert await pos_mgr._check_cooldown("BTCUSDT") is False

    @pytest.mark.asyncio
    async def test_cooldown_active(self, pos_mgr, redis_client):
        redis_client.get.return_value = b"1"
        assert await pos_mgr._check_cooldown("BTCUSDT") is True

    @pytest.mark.asyncio
    async def test_set_cooldown(self, pos_mgr, redis_client):
        await pos_mgr._set_cooldown("BTCUSDT")
        redis_client.setex.assert_called_once_with(
            "karsa:partial_exit_cooldown:BTCUSDT", COOLDOWN_SEC, "1"
        )


# ── Partial Exits ──────────────────────────────────────────────────────────────

class TestPartialExits:
    @pytest.mark.asyncio
    async def test_empty_positions(self, pos_mgr):
        result = await pos_mgr.check_partial_exits([])
        assert result == []

    @pytest.mark.asyncio
    async def test_skip_closed_position(self, pos_mgr):
        positions = [{
            "id": 1,
            "ticker": "BTCUSDT",
            "entry_price": Decimal("65000"),
            "trailing_stop_price": Decimal("63000"),
            "partial_exits_taken": 0,
            "status": "CLOSED",
        }]
        result = await pos_mgr.check_partial_exits(positions)
        assert result == []

    @pytest.mark.asyncio
    async def test_no_exit_below_1r(self, pos_mgr, redis_client):
        """Price below +1R should not trigger exit."""
        positions = [{
            "id": 1,
            "ticker": "BTCUSDT",
            "entry_price": Decimal("65000"),
            "trailing_stop_price": Decimal("63000"),  # R = 2000
            "partial_exits_taken": 0,
            "status": "OPEN",
            "direction": "LONG",
        }]
        with patch.object(pos_mgr, '_get_current_price', return_value=Decimal("66000")):
            result = await pos_mgr.check_partial_exits(positions)
        assert result == []

    @pytest.mark.asyncio
    async def test_exit_at_1r(self, pos_mgr, redis_client):
        """Price at +1R should trigger first partial exit (33%)."""
        positions = [{
            "id": 1,
            "ticker": "BTCUSDT",
            "entry_price": Decimal("65000"),
            "trailing_stop_price": Decimal("63000"),  # R = 2000, target = 67000
            "partial_exits_taken": 0,
            "status": "OPEN",
            "direction": "LONG",
        }]
        with patch.object(pos_mgr, '_get_current_price', return_value=Decimal("67500")):
            result = await pos_mgr.check_partial_exits(positions)
        assert len(result) == 1
        assert result[0]["reason"] == "partial_1r"
        assert result[0]["exit_pct"] == 33


# ── Time Exits ─────────────────────────────────────────────────────────────────

class TestTimeExits:
    @pytest.mark.asyncio
    async def test_empty_positions(self, pos_mgr):
        result = await pos_mgr.check_time_exits([])
        assert result == []

    @pytest.mark.asyncio
    async def test_skip_recent_position(self, pos_mgr):
        """Position < 72h old should not trigger time exit."""
        positions = [{
            "id": 1,
            "ticker": "BTCUSDT",
            "entry_price": Decimal("65000"),
            "created_at": datetime.now(timezone.utc) - timedelta(hours=24),
            "status": "OPEN",
            "direction": "LONG",
        }]
        result = await pos_mgr.check_time_exits(positions)
        assert result == []

    @pytest.mark.asyncio
    async def test_exit_old_position_with_gain(self, pos_mgr, redis_client):
        """Position > 72h with >1% gain should trigger time exit."""
        positions = [{
            "id": 1,
            "ticker": "BTCUSDT",
            "entry_price": Decimal("65000"),
            "created_at": datetime.now(timezone.utc) - timedelta(hours=80),
            "status": "OPEN",
            "direction": "LONG",
        }]
        with patch.object(pos_mgr, '_get_current_price', return_value=Decimal("66000")):
            result = await pos_mgr.check_time_exits(positions)
        assert len(result) == 1
        assert result[0]["reason"] == "time_exit"

    @pytest.mark.asyncio
    async def test_no_exit_old_position_no_gain(self, pos_mgr, redis_client):
        """Position > 72h but <1% gain should not trigger time exit."""
        positions = [{
            "id": 1,
            "ticker": "BTCUSDT",
            "entry_price": Decimal("65000"),
            "created_at": datetime.now(timezone.utc) - timedelta(hours=80),
            "status": "OPEN",
            "direction": "LONG",
        }]
        with patch.object(pos_mgr, '_get_current_price', return_value=Decimal("65100")):
            result = await pos_mgr.check_time_exits(positions)
        assert result == []
