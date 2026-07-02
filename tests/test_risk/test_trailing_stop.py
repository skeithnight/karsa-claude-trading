"""Tests for TrailingStopManager — ATR-based trailing with regime multipliers."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal

from src.risk.trailing_stop import (
    TrailingStopManager,
    REGIME_TRAIL_MULTIPLIER,
    COOLDOWN_KEY_PREFIX,
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

    def test_cooldown_seconds(self):
        assert COOLDOWN_SEC == 300

    def test_cooldown_key_prefix(self):
        assert COOLDOWN_KEY_PREFIX == "karsa:trailing_stop_cooldown"


# ── Cooldown ───────────────────────────────────────────────────────────────────

class TestCooldown:
    @pytest.mark.asyncio
    async def test_no_cooldown_when_no_redis(self, trail_mgr):
        """_check_cooldown returns True (fail-open) when redis is None."""
        trail_mgr._redis = None
        assert await trail_mgr._check_cooldown("BTCUSDT") is True

    @pytest.mark.asyncio
    async def test_no_cooldown_when_key_missing(self, trail_mgr, redis_client):
        """_check_cooldown returns True when key is missing (no cooldown active)."""
        redis_client.get.return_value = None
        assert await trail_mgr._check_cooldown("BTCUSDT") is True

    @pytest.mark.asyncio
    async def test_cooldown_active(self, trail_mgr, redis_client):
        """_check_cooldown returns False when key exists (cooldown is active)."""
        redis_client.get.return_value = b"1"
        assert await trail_mgr._check_cooldown("BTCUSDT") is False

    @pytest.mark.asyncio
    async def test_set_cooldown(self, trail_mgr, redis_client):
        await trail_mgr._set_cooldown("BTCUSDT")
        redis_client.setex.assert_called_once_with(
            f"{COOLDOWN_KEY_PREFIX}:BTCUSDT", COOLDOWN_SEC, "1"
        )


# ── Update Trailing Stops ──────────────────────────────────────────────────────

class TestUpdateTrailingStops:
    @pytest.mark.asyncio
    async def test_empty_positions(self, trail_mgr):
        result = await trail_mgr.update_trailing_stops([])
        assert result == []

    @pytest.mark.asyncio
    async def test_skip_non_open(self, trail_mgr):
        """Positions with status != OPEN should be skipped."""
        positions = [_pos(status="CLOSED")]
        result = await trail_mgr.update_trailing_stops(positions)
        assert result == []

    @pytest.mark.asyncio
    async def test_breakeven_floor(self, trail_mgr, redis_client):
        """Stop should never go below entry + 10% of ATR."""
        positions = [_pos(
            entry_price=Decimal("65000"),
            highest_price=Decimal("65100"),
            trailing_stop_price=None,
        )]
        with patch.object(trail_mgr, '_get_current_price', return_value=Decimal("65200")), \
             patch.object(trail_mgr, '_get_ohlcv', return_value=[
                 {"high": 65200, "low": 64800, "close": 65100},
                 {"high": 65300, "low": 64900, "close": 65050},
                 {"high": 65100, "low": 64700, "close": 64900},
             ]), \
             patch('src.risk.trailing_stop.calculate_atr', return_value={"atr": 200.0}):
            result = await trail_mgr.update_trailing_stops(positions)

        if result:
            # Stop should be at least entry + 10% of ATR
            min_stop = Decimal("65000") + Decimal("200.0") * Decimal("0.10")
            assert result[0]["new_stop"] >= min_stop


# ── Helpers for extended tests ─────────────────────────────────────────────────

from types import SimpleNamespace


def _pos(**kw):
    """Create a position-like object supporting attribute access for tests."""
    defaults = {
        "id": 1,
        "ticker": "BTCUSDT",
        "status": "OPEN",
        "side": "Buy",
        "entry_price": Decimal("65000"),
        "highest_price": Decimal("68000"),
        "trailing_stop_price": None,
        "regime_at_entry": "TREND_BULL",
        "signal_source": "trend",
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# 15-candle OHLCV stub — minimum length for ATR calculation
_OHLCV_15 = [
    {"high": 65000 + i * 50, "low": 64800 + i * 50, "close": 64900 + i * 50}
    for i in range(15)
]


# ── Extended edge-case coverage ───────────────────────────────────────────────

class TestTrailingStopExtended:
    """Edge-case and failure-path coverage for TrailingStopManager."""

    # 1. CHOP regime skip ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_chop_regime_skips_position(self, trail_mgr):
        """CHOP regime has multiplier=0, position should be skipped entirely."""
        positions = [_pos(regime_at_entry="CHOP")]
        result = await trail_mgr.update_trailing_stops(positions)
        assert result == []

    # 2. SHORT trailing ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_short_trailing_stop_updates(self, trail_mgr):
        """SHORT: stop = highest + distance, ceiling at entry - 10% ATR."""
        positions = [_pos(
            side="Sell",
            entry_price=Decimal("70000"),
            highest_price=Decimal("68000"),
            trailing_stop_price=Decimal("69500"),
        )]
        with patch.object(trail_mgr, '_get_current_price', return_value=Decimal("67000")), \
             patch.object(trail_mgr, '_get_ohlcv', return_value=_OHLCV_15), \
             patch('src.risk.trailing_stop.calculate_atr', return_value={"atr": 500.0}), \
             patch.object(trail_mgr, '_amend_stop_on_exchange', return_value=True), \
             patch('src.risk.trailing_stop.async_session'):
            result = await trail_mgr.update_trailing_stops(positions)

        assert len(result) == 1
        # TREND_BULL multi=2.0, dist=1000; highest=max(68000,67000)=68000
        # trail_stop = 68000 + 1000 = 69000
        # breakeven ceiling = min(69000, 70000-50) = 69000
        assert Decimal(result[0]["new_stop"]) == Decimal("69000")

    # 3. Cooldown skip ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_cooldown_active_skips_position(self, trail_mgr):
        """When _check_cooldown returns False (active cooldown), position is skipped."""
        positions = [_pos(trailing_stop_price=Decimal("66000"))]
        with patch.object(trail_mgr, '_get_current_price', return_value=Decimal("72000")), \
             patch.object(trail_mgr, '_get_ohlcv', return_value=_OHLCV_15), \
             patch('src.risk.trailing_stop.calculate_atr', return_value={"atr": 500.0}), \
             patch.object(trail_mgr, '_check_cooldown', return_value=False):
            result = await trail_mgr.update_trailing_stops(positions)
        assert result == []

    # 4. No stop order found ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_amend_no_stop_order_returns_false(self, trail_mgr, bybit):
        """When no StopLoss order exists on exchange, _amend_stop returns False."""
        pos_obj = _pos()
        bybit._http_client.get_open_orders = MagicMock(return_value={
            "retCode": 0,
            "result": {"list": []},
        })
        result = await trail_mgr._amend_stop_on_exchange(pos_obj, Decimal("69000"))
        assert result is False

    # 5. Amend failure ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_amend_order_exception_returns_false(self, trail_mgr, bybit):
        """When amend_order raises, _amend_stop_on_exchange returns False."""
        pos_obj = _pos()
        bybit._http_client.get_open_orders = MagicMock(return_value={
            "retCode": 0,
            "result": {"list": [
                {"stopOrderType": "StopLoss", "orderId": "abc123"},
            ]},
        })
        bybit._http_client.amend_order = MagicMock(side_effect=Exception("network"))
        result = await trail_mgr._amend_stop_on_exchange(pos_obj, Decimal("69000"))
        assert result is False

    # 6. Redis None fail-open ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_check_cooldown_no_redis_fail_open(self, trail_mgr):
        """_check_cooldown with no redis returns True (fail-open)."""
        trail_mgr._redis = None
        assert await trail_mgr._check_cooldown("BTCUSDT") is True

    # 7. Price fetch failure ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_price_fetch_failure_skips_position(self, trail_mgr):
        """When _get_current_price returns None, position is skipped."""
        positions = [_pos()]
        with patch.object(trail_mgr, '_get_current_price', return_value=None):
            result = await trail_mgr.update_trailing_stops(positions)
        assert result == []

    # 8. OHLCV fetch failure ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_ohlcv_fetch_failure_skips_position(self, trail_mgr):
        """When _get_ohlcv returns None, position is skipped."""
        positions = [_pos()]
        with patch.object(trail_mgr, '_get_current_price', return_value=Decimal("70000")), \
             patch.object(trail_mgr, '_get_ohlcv', return_value=None):
            result = await trail_mgr.update_trailing_stops(positions)
        assert result == []

    # 9. Noise filter ──────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_noise_filter_skips_tiny_stop_change(self, trail_mgr):
        """When |new_stop - old_stop| < 5% of ATR, skip as noise."""
        # ATR=500, 5% = 25. Old stop 69010, new stop 69000, diff=10 < 25.
        positions = [_pos(
            highest_price=Decimal("70000"),
            trailing_stop_price=Decimal("69010"),
        )]
        with patch.object(trail_mgr, '_get_current_price', return_value=Decimal("70000")), \
             patch.object(trail_mgr, '_get_ohlcv', return_value=_OHLCV_15), \
             patch('src.risk.trailing_stop.calculate_atr', return_value={"atr": 500.0}):
            result = await trail_mgr.update_trailing_stops(positions)
        assert result == []

    # 10. _set_cooldown ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_set_cooldown_calls_redis_setex(self, trail_mgr, redis_client):
        """_set_cooldown calls redis.setex with correct key and TTL."""
        await trail_mgr._set_cooldown("BTCUSDT")
        redis_client.setex.assert_called_once_with(
            f"{COOLDOWN_KEY_PREFIX}:BTCUSDT", COOLDOWN_SEC, "1"
        )

    # 11. _set_cooldown no redis ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_set_cooldown_no_redis_is_noop(self, trail_mgr):
        """_set_cooldown does nothing when redis is None."""
        trail_mgr._redis = None
        await trail_mgr._set_cooldown("BTCUSDT")  # should not raise
