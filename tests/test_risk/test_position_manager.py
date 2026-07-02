"""Tests for PositionManager — partial exits and time-based exits."""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from src.risk.position_manager import (
    PositionManager,
    PARTIAL_EXIT_TARGETS,
    TIME_EXIT_MAX_HOURS,
    TIME_EXIT_MIN_GAIN_PCT,
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
        assert len(PARTIAL_EXIT_TARGETS) >= 1
        assert PARTIAL_EXIT_TARGETS[0]["r_multiple"] == 1.0
        assert PARTIAL_EXIT_TARGETS[0]["exit_pct"] == 50

    def test_time_exit_max_hours(self):
        assert TIME_EXIT_MAX_HOURS == 48

    def test_time_exit_min_gain(self):
        assert TIME_EXIT_MIN_GAIN_PCT == Decimal("1.0")


# ── Partial Exits ──────────────────────────────────────────────────────────────

class TestPartialExits:
    @pytest.mark.asyncio
    async def test_empty_positions(self, pos_mgr):
        result = await pos_mgr.check_partial_exits([])
        assert result == []

    @pytest.mark.asyncio
    async def test_skip_closed_position(self, pos_mgr):
        positions = [_make_position(status="CLOSED")]
        result = await pos_mgr.check_partial_exits(positions)
        assert result == []

    @pytest.mark.asyncio
    async def test_no_exit_below_1r(self, pos_mgr):
        """Price below +1R should not trigger exit."""
        pos = _make_position(
            entry_price=Decimal("65000"),
            current_price=Decimal("66000"),
            stop_loss=Decimal("63000"),  # R = 2000, target = 67000
        )
        result = await pos_mgr.check_partial_exits([pos])
        assert result == []

    @pytest.mark.asyncio
    async def test_exit_at_1r(self, pos_mgr):
        """Price at +1R should trigger first partial exit (50%)."""
        pos = _make_position(
            entry_price=Decimal("65000"),
            current_price=Decimal("67500"),
            stop_loss=Decimal("63000"),  # R = 2000, target = 67000
        )
        result = await pos_mgr.check_partial_exits([pos])
        assert len(result) == 1
        assert result[0]["reason"] == "partial_1r"
        assert result[0]["exit_pct"] == 50


# ── Time Exits ─────────────────────────────────────────────────────────────────

class TestTimeExits:
    @pytest.mark.asyncio
    async def test_empty_positions(self, pos_mgr):
        result = await pos_mgr.check_time_exits([])
        assert result == []

    @pytest.mark.asyncio
    async def test_skip_recent_position(self, pos_mgr):
        """Position < 48h old should not trigger time exit."""
        pos = _make_position(
            opened_at=datetime.now(timezone.utc) - timedelta(hours=24),
            current_price=Decimal("65100"),
        )
        result = await pos_mgr.check_time_exits([pos])
        assert result == []

    @pytest.mark.asyncio
    async def test_exit_old_losing_position(self, pos_mgr):
        """Position > 48h with <1% gain should trigger time exit."""
        pos = _make_position(
            opened_at=datetime.now(timezone.utc) - timedelta(hours=80),
            current_price=Decimal("65100"),  # 0.15% gain, below 1% threshold
        )
        result = await pos_mgr.check_time_exits([pos])
        assert len(result) == 1
        assert result[0]["action"] == "time_exit"

    @pytest.mark.asyncio
    async def test_no_exit_old_profitable_position(self, pos_mgr):
        """Position > 48h but with >1% gain should NOT trigger time exit."""
        pos = _make_position(
            opened_at=datetime.now(timezone.utc) - timedelta(hours=80),
            current_price=Decimal("66000"),  # 1.54% gain, above 1% threshold
        )
        result = await pos_mgr.check_time_exits([pos])
        assert result == []


# ---------------------------------------------------------------------------
# Helpers for new tests (SimpleNamespace so source attribute access works)
# ---------------------------------------------------------------------------

def _make_position(**overrides):
    """Return a SimpleNamespace with sensible defaults for a LONG position."""
    defaults = dict(
        id=1,
        ticker="BTCUSDT",
        entry_price=Decimal("65000"),
        current_price=Decimal("66000"),
        stop_loss=Decimal("64000"),
        size=Decimal("0.1"),
        side="Buy",
        status="OPEN",
        partial_exits_taken=0,
        opened_at=datetime.now(timezone.utc) - timedelta(hours=2),
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        leverage=10,
        regime_at_entry="TREND_BULL",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ===================================================================
# 1. SHORT profit calc — check_partial_exits
# ===================================================================

class TestPartialExitSHORT:
    """SHORT positions: risk = stop - entry, reward = entry - current."""

    @pytest.mark.asyncio
    async def test_short_at_1r_triggers_partial(self, pos_mgr, redis_client):
        """SHORT at exactly 1R should trigger partial exit."""
        # entry=65000, stop=66000, current=64000
        # risk = 66000 - 65000 = 1000, reward = 65000 - 64000 = 1000 => R=1.0
        pos = _make_position(
            side="Sell",
            entry_price=Decimal("65000"),
            stop_loss=Decimal("66000"),
            current_price=Decimal("64000"),
        )
        result = await pos_mgr.check_partial_exits([pos])
        assert len(result) == 1
        assert result[0]["exit_pct"] == 50
        assert result[0]["reason"] == "partial_1r"

    @pytest.mark.asyncio
    async def test_short_below_1r_no_trigger(self, pos_mgr, redis_client):
        """SHORT below 1R should NOT trigger partial exit."""
        # entry=65000, stop=66000, current=64500
        # risk=1000, reward=500 => R=0.5
        pos = _make_position(
            side="Sell",
            entry_price=Decimal("65000"),
            stop_loss=Decimal("66000"),
            current_price=Decimal("64500"),
        )
        result = await pos_mgr.check_partial_exits([pos])
        assert result == []

    @pytest.mark.asyncio
    async def test_short_losing_no_trigger(self, pos_mgr, redis_client):
        """SHORT in loss (price above entry) should NOT trigger."""
        # entry=65000, stop=66000, current=65500
        # risk=1000, reward=-500 => negative R
        pos = _make_position(
            side="Sell",
            entry_price=Decimal("65000"),
            stop_loss=Decimal("66000"),
            current_price=Decimal("65500"),
        )
        result = await pos_mgr.check_partial_exits([pos])
        assert result == []


# ===================================================================
# 2. Already-exited target — partial_exits_taken exhausted
# ===================================================================

class TestPartialExitExhausted:
    """When all partial targets have been taken, skip."""

    @pytest.mark.asyncio
    async def test_all_partials_taken_skips(self, pos_mgr, redis_client):
        """partial_exits_taken >= len(PARTIAL_EXIT_TARGETS) should skip."""
        pos = _make_position(
            partial_exits_taken=len(PARTIAL_EXIT_TARGETS),
            entry_price=Decimal("65000"),
            stop_loss=Decimal("64000"),
            current_price=Decimal("67000"),
        )
        result = await pos_mgr.check_partial_exits([pos])
        assert result == []

    @pytest.mark.asyncio
    async def test_excess_partials_taken_skips(self, pos_mgr, redis_client):
        """partial_exits_taken > len(PARTIAL_EXIT_TARGETS) should also skip."""
        pos = _make_position(
            partial_exits_taken=len(PARTIAL_EXIT_TARGETS) + 5,
            entry_price=Decimal("65000"),
            stop_loss=Decimal("64000"),
            current_price=Decimal("70000"),
        )
        result = await pos_mgr.check_partial_exits([pos])
        assert result == []


# ===================================================================
# 3. execute_partial_exit edge cases
# ===================================================================

class TestExecutePartialExit:
    """Edge cases for the execute_partial_exit method."""

    @pytest.mark.asyncio
    async def test_position_not_found(self, pos_mgr, redis_client):
        """Returns error when position is not found in DB."""
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("src.risk.position_manager.async_session", return_value=mock_session):
            result = await pos_mgr.execute_partial_exit(999, 50, "partial_1r")

        assert result["success"] is False
        assert "not found" in result["error"].lower() or "not open" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_zero_exit_quantity(self, pos_mgr, redis_client):
        """Returns error when computed exit quantity is zero."""
        mock_pos = MagicMock()
        mock_pos.status = "OPEN"
        mock_pos.size = Decimal("0")
        mock_pos.side = "Buy"
        mock_pos.ticker = "BTCUSDT"
        mock_pos.id = 1

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_pos)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("src.risk.position_manager.async_session", return_value=mock_session):
            result = await pos_mgr.execute_partial_exit(1, 50, "partial_1r")

        assert result["success"] is False
        assert "zero" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_sor_failure(self, pos_mgr, redis_client):
        """Returns error when SOR execute_order fails."""
        mock_pos = MagicMock()
        mock_pos.status = "OPEN"
        mock_pos.size = Decimal("0.1")
        mock_pos.side = "Buy"
        mock_pos.ticker = "BTCUSDT"
        mock_pos.id = 1

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_pos)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_sor_instance = AsyncMock()
        mock_sor_instance.execute_order = AsyncMock(
            return_value={"success": False, "error": "insufficient liquidity"}
        )

        with (
            patch("src.risk.position_manager.async_session", return_value=mock_session),
            patch("src.risk.sor.SmartOrderRouter", return_value=mock_sor_instance),
        ):
            result = await pos_mgr.execute_partial_exit(1, 50, "partial_1r")

        assert result["success"] is False
        assert "sor" in result["error"].lower() or "liquidity" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_general_exception(self, pos_mgr, redis_client):
        """Returns error on unexpected exception during execution."""
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=RuntimeError("db connection lost"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("src.risk.position_manager.async_session", return_value=mock_session):
            result = await pos_mgr.execute_partial_exit(1, 50, "partial_1r")

        assert result["success"] is False
        assert "db connection lost" in result["error"]


# ===================================================================
# 4. Time exit SHORT — stale short should trigger
# ===================================================================

class TestTimeExitSHORT:
    """Time-based exits for SHORT positions."""

    @pytest.mark.asyncio
    async def test_stale_short_losing_triggers(self, pos_mgr, redis_client):
        """Stale SHORT with price above entry (losing) should trigger time exit."""
        # entry=65000, current=66000 => SHORT gain = (65000-66000)/65000*100 = -1.54%
        # -1.54% < 1.0% threshold => should trigger
        pos = _make_position(
            side="Sell",
            entry_price=Decimal("65000"),
            current_price=Decimal("66000"),
            opened_at=datetime.now(timezone.utc) - timedelta(hours=72),
            created_at=datetime.now(timezone.utc) - timedelta(hours=72),
        )
        result = await pos_mgr.check_time_exits([pos])
        assert len(result) == 1
        assert result[0]["action"] == "time_exit"
        assert result[0]["gain_pct"] < 0


# ===================================================================
# 5. Profitable SHORT — gain >= 1% should be skipped
# ===================================================================

class TestProfitableSHORT:
    """SHORT position with enough profit should NOT be time-exited."""

    @pytest.mark.asyncio
    async def test_profitable_short_skipped(self, pos_mgr, redis_client):
        """Stale SHORT with >= 1% gain should be left running."""
        # entry=65000, current=64000 => SHORT gain = (65000-64000)/65000*100 = 1.54%
        # 1.54% >= 1.0% threshold => skip (let it run)
        pos = _make_position(
            side="Sell",
            entry_price=Decimal("65000"),
            current_price=Decimal("64000"),
            opened_at=datetime.now(timezone.utc) - timedelta(hours=72),
            created_at=datetime.now(timezone.utc) - timedelta(hours=72),
        )
        result = await pos_mgr.check_time_exits([pos])
        assert result == []


# ===================================================================
# 6. Fresh position — < 48h old should be skipped
# ===================================================================

class TestFreshPosition:
    """Positions opened recently should not be time-exited."""

    @pytest.mark.asyncio
    async def test_fresh_long_skipped(self, pos_mgr, redis_client):
        """LONG opened < 48h ago with small gain should NOT trigger."""
        pos = _make_position(
            side="Buy",
            entry_price=Decimal("65000"),
            current_price=Decimal("65100"),
            opened_at=datetime.now(timezone.utc) - timedelta(hours=24),
            created_at=datetime.now(timezone.utc) - timedelta(hours=24),
        )
        result = await pos_mgr.check_time_exits([pos])
        assert result == []

    @pytest.mark.asyncio
    async def test_fresh_short_skipped(self, pos_mgr, redis_client):
        """SHORT opened < 48h ago should NOT trigger time exit."""
        pos = _make_position(
            side="Sell",
            entry_price=Decimal("65000"),
            current_price=Decimal("65100"),
            opened_at=datetime.now(timezone.utc) - timedelta(hours=12),
            created_at=datetime.now(timezone.utc) - timedelta(hours=12),
        )
        result = await pos_mgr.check_time_exits([pos])
        assert result == []


# ===================================================================
# 7. Exception handling — attribute/data errors skipped gracefully
# ===================================================================

class TestTimeExitExceptionHandling:
    """Exceptions in individual positions should be caught, not crash the loop."""

    @pytest.mark.asyncio
    async def test_bad_attribute_skipped_gracefully(self, pos_mgr, redis_client):
        """Position with missing attributes should be skipped without raising."""
        # Missing opened_at => AttributeError on pos.opened_at => caught by except block
        bad_pos = SimpleNamespace(
            id=99,
            ticker="BROKEN",
            status="OPEN",
            side="Buy",
            entry_price=Decimal("65000"),
            current_price=Decimal("65000"),
        )
        good_pos = _make_position(
            id=2,
            side="Buy",
            entry_price=Decimal("65000"),
            current_price=Decimal("65100"),
            opened_at=datetime.now(timezone.utc) - timedelta(hours=72),
            created_at=datetime.now(timezone.utc) - timedelta(hours=72),
        )
        result = await pos_mgr.check_time_exits([bad_pos, good_pos])
        # Only good_pos should appear; bad_pos is skipped via exception handler
        assert len(result) == 1
        assert result[0]["position_id"] == 2

    @pytest.mark.asyncio
    async def test_partial_exit_exception_skips_position(self, pos_mgr, redis_client):
        """Exception during partial exit check should skip that position, not crash."""
        bad_pos = SimpleNamespace(
            id=99,
            ticker="BROKEN",
            status="OPEN",
            partial_exits_taken=0,
        )
        # Missing entry_price, current_price, stop_loss => will raise AttributeError
        good_pos = _make_position(
            id=2,
            entry_price=Decimal("65000"),
            stop_loss=Decimal("64000"),
            current_price=Decimal("66000"),
        )
        result = await pos_mgr.check_partial_exits([bad_pos, good_pos])
        # bad_pos raises, good_pos triggers at 1R
        assert len(result) == 1
        assert result[0]["position_id"] == 2


# ===================================================================
# 8. Zero entry price — division guard
# ===================================================================

class TestZeroEntryPrice:
    """Positions with entry_price=0 should be skipped to avoid ZeroDivisionError."""

    @pytest.mark.asyncio
    async def test_partial_exit_zero_entry_skipped(self, pos_mgr, redis_client):
        """Partial exit check should skip position with entry_price=0."""
        pos = _make_position(
            entry_price=Decimal("0"),
            current_price=Decimal("66000"),
            stop_loss=Decimal("64000"),
        )
        result = await pos_mgr.check_partial_exits([pos])
        assert result == []

    @pytest.mark.asyncio
    async def test_time_exit_zero_entry_skipped(self, pos_mgr, redis_client):
        """Time exit check should skip position with entry_price=0."""
        pos = _make_position(
            entry_price=Decimal("0"),
            current_price=Decimal("66000"),
            opened_at=datetime.now(timezone.utc) - timedelta(hours=80),
            created_at=datetime.now(timezone.utc) - timedelta(hours=80),
        )
        result = await pos_mgr.check_time_exits([pos])
        assert result == []
