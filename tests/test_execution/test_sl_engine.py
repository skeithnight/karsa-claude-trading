"""Tests for src/execution/sl_engine.py — StopLossEngine."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.execution.sl_engine import REDIS_TICK_CHANNEL, StopLossEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tick(ticker: str, price: float) -> dict:
    return {"ticker": ticker, "price": price, "ts": int(time.time() * 1000)}


def _position(ticker: str, side: str, size: float, stop_loss: float) -> dict:
    return {"symbol": ticker, "side": side, "size": size, "stopLoss": stop_loss}


# ---------------------------------------------------------------------------
# _check_tick — no breach
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tick_above_sl_for_long_no_breach(fake_redis, mock_bybit):
    """LONG position: price above stop-loss should NOT trigger."""
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._position_cache = {
        "BTCUSDT": {"stop_loss": 59000.0, "side": "Buy", "size": 0.5},
    }
    engine._last_sync = time.time()  # prevent sync trigger

    await engine._check_tick(_tick("BTCUSDT", 60000.0))

    mock_bybit.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_tick_below_sl_for_short_no_breach(fake_redis, mock_bybit):
    """SHORT position: price below stop-loss should NOT trigger."""
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._position_cache = {
        "ETHUSDT": {"stop_loss": 3600.0, "side": "Sell", "size": 1.0},
    }
    engine._last_sync = time.time()

    await engine._check_tick(_tick("ETHUSDT", 3500.0))

    mock_bybit.place_order.assert_not_called()


# ---------------------------------------------------------------------------
# _check_tick — breach
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tick_below_sl_for_long_breaches(fake_redis, mock_bybit):
    """LONG position: price at or below SL triggers close."""
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._position_cache = {
        "BTCUSDT": {"stop_loss": 59000.0, "side": "Buy", "size": 0.5},
    }
    engine._last_sync = time.time()

    mock_bybit.place_order.return_value = {"orderId": "sl_fill_001"}

    await engine._check_tick(_tick("BTCUSDT", 58900.0))

    # Should have placed a Sell (close Buy) market order
    mock_bybit.place_order.assert_called_once()
    call_kwargs = mock_bybit.place_order.call_args
    assert call_kwargs.kwargs["symbol"] == "BTCUSDT"
    assert call_kwargs.kwargs["side"] == "Sell"
    assert call_kwargs.kwargs["order_type"] == "Market"
    assert call_kwargs.kwargs["reduce_only"] is True

    # Position should be removed from cache
    assert "BTCUSDT" not in engine._position_cache


@pytest.mark.asyncio
async def test_tick_at_sl_for_long_breaches(fake_redis, mock_bybit):
    """LONG: price exactly at SL also triggers (<= comparison)."""
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._position_cache = {
        "BTCUSDT": {"stop_loss": 59000.0, "side": "Buy", "size": 0.5},
    }
    engine._last_sync = time.time()
    mock_bybit.place_order.return_value = {"orderId": "sl_exact"}

    await engine._check_tick(_tick("BTCUSDT", 59000.0))
    mock_bybit.place_order.assert_called_once()


@pytest.mark.asyncio
async def test_tick_above_sl_for_short_breaches(fake_redis, mock_bybit):
    """SHORT position: price at or above SL triggers close."""
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._position_cache = {
        "ETHUSDT": {"stop_loss": 3600.0, "side": "Sell", "size": 1.0},
    }
    engine._last_sync = time.time()

    mock_bybit.place_order.return_value = {"orderId": "sl_fill_002"}

    await engine._check_tick(_tick("ETHUSDT", 3610.0))

    mock_bybit.place_order.assert_called_once()
    call_kwargs = mock_bybit.place_order.call_args
    assert call_kwargs.kwargs["side"] == "Buy"  # close Sell = Buy


@pytest.mark.asyncio
async def test_tick_no_cached_position_ignored(fake_redis, mock_bybit):
    """Tick for ticker with no cached position should be a no-op."""
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._position_cache = {}
    engine._last_sync = time.time()

    await engine._check_tick(_tick("UNKNOWN", 100.0))
    mock_bybit.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_tick_missing_ticker_or_price_ignored(fake_redis, mock_bybit):
    """Ticks with missing data should be ignored."""
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._last_sync = time.time()

    await engine._check_tick({"ticker": "", "price": 100.0})
    await engine._check_tick({"ticker": "BTCUSDT", "price": 0})
    await engine._check_tick({"ticker": "BTCUSDT"})  # missing price key

    mock_bybit.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_tick_zero_sl_ignored(fake_redis, mock_bybit):
    """If stop_loss is 0 (not set), no breach check should happen."""
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._position_cache = {
        "BTCUSDT": {"stop_loss": 0, "side": "Buy", "size": 0.5},
    }
    engine._last_sync = time.time()

    await engine._check_tick(_tick("BTCUSDT", 50000.0))
    mock_bybit.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_tick_zero_size_ignored(fake_redis, mock_bybit):
    """If size is 0, position is effectively closed — skip."""
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._position_cache = {
        "BTCUSDT": {"stop_loss": 59000.0, "side": "Buy", "size": 0},
    }
    engine._last_sync = time.time()

    await engine._check_tick(_tick("BTCUSDT", 58000.0))
    mock_bybit.place_order.assert_not_called()


# ---------------------------------------------------------------------------
# _execute_close — success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_close_publishes_event(fake_redis, mock_bybit):
    """On successful close, event should be published to Redis."""
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._position_cache = {
        "BTCUSDT": {"stop_loss": 59000.0, "side": "Buy", "size": 0.5},
    }

    mock_bybit.place_order.return_value = {"orderId": "close_001"}

    await engine._execute_close("BTCUSDT", "Buy", 0.5, 58900.0, 59000.0)

    # Check publish was called
    assert len(fake_redis._published) == 1
    channel, payload = fake_redis._published[0]
    assert channel == "karsa:events:sl_triggered"
    data = json.loads(payload)
    assert data["ticker"] == "BTCUSDT"
    assert data["trigger_price"] == 58900.0
    assert data["stop_loss"] == 59000.0
    assert data["order_id"] == "close_001"

    # Cache should be cleared
    assert "BTCUSDT" not in engine._position_cache


# ---------------------------------------------------------------------------
# _execute_close — failure path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_close_failure_no_cache_removal(fake_redis, mock_bybit):
    """If order returns no orderId, cache should NOT be cleared."""
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._position_cache = {
        "BTCUSDT": {"stop_loss": 59000.0, "side": "Buy", "size": 0.5},
    }

    mock_bybit.place_order.return_value = {"error": "Insufficient balance"}

    await engine._execute_close("BTCUSDT", "Buy", 0.5, 58900.0, 59000.0)

    # Position should still be in cache
    assert "BTCUSDT" in engine._position_cache


@pytest.mark.asyncio
async def test_execute_close_exception_is_caught(fake_redis, mock_bybit):
    """Exceptions during execution should not propagate."""
    engine = StopLossEngine(fake_redis, mock_bybit)
    mock_bybit.place_order.side_effect = Exception("Network timeout")

    # Should not raise
    await engine._execute_close("BTCUSDT", "Buy", 0.5, 58900.0, 59000.0)


# ---------------------------------------------------------------------------
# _sync_positions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_positions_populates_cache(fake_redis, mock_bybit):
    engine = StopLossEngine(fake_redis, mock_bybit)
    mock_bybit.get_positions.return_value = [
        _position("BTCUSDT", "Buy", 0.5, 59000.0),
        _position("ETHUSDT", "Sell", 1.0, 3600.0),
    ]

    await engine._sync_positions()

    assert "BTCUSDT" in engine._position_cache
    assert engine._position_cache["BTCUSDT"]["stop_loss"] == 59000.0
    assert engine._position_cache["BTCUSDT"]["side"] == "Buy"
    assert "ETHUSDT" in engine._position_cache


@pytest.mark.asyncio
async def test_sync_positions_skips_zero_size(fake_redis, mock_bybit):
    engine = StopLossEngine(fake_redis, mock_bybit)
    mock_bybit.get_positions.return_value = [
        _position("BTCUSDT", "Buy", 0.5, 59000.0),
        _position("DOGEUSDT", "Buy", 0, 0.1),  # zero size
    ]

    await engine._sync_positions()

    assert "BTCUSDT" in engine._position_cache
    assert "DOGEUSDT" not in engine._position_cache


@pytest.mark.asyncio
async def test_sync_positions_fetches_sl_from_orders(fake_redis, mock_bybit):
    """If position has no stopLoss, fetches from open stop orders."""
    engine = StopLossEngine(fake_redis, mock_bybit)
    mock_bybit.get_positions.return_value = [
        {"symbol": "BTCUSDT", "side": "Buy", "size": 0.5, "stopLoss": 0},
    ]
    mock_bybit.get_open_orders.return_value = [
        {"stopOrderType": "StopLoss", "triggerPrice": "58000"},
    ]

    await engine._sync_positions()

    assert engine._position_cache["BTCUSDT"]["stop_loss"] == 58000.0


@pytest.mark.asyncio
async def test_sync_positions_clears_old_cache(fake_redis, mock_bybit):
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._position_cache = {"OLDCOIN": {"stop_loss": 100, "side": "Buy", "size": 1.0}}

    mock_bybit.get_positions.return_value = [
        _position("BTCUSDT", "Buy", 0.5, 59000.0),
    ]

    await engine._sync_positions()

    assert "OLDCOIN" not in engine._position_cache
    assert "BTCUSDT" in engine._position_cache


@pytest.mark.asyncio
async def test_sync_positions_handles_none_response(fake_redis, mock_bybit):
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._position_cache = {"BTCUSDT": {"stop_loss": 100, "side": "Buy", "size": 1.0}}
    mock_bybit.get_positions.return_value = None

    await engine._sync_positions()

    # Cache should remain unchanged
    assert "BTCUSDT" in engine._position_cache


@pytest.mark.asyncio
async def test_sync_positions_handles_exception(fake_redis, mock_bybit):
    engine = StopLossEngine(fake_redis, mock_bybit)
    mock_bybit.get_positions.side_effect = Exception("Bybit down")

    # Should not raise
    await engine._sync_positions()


# ---------------------------------------------------------------------------
# _fetch_stop_price
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_stop_price_from_orders(fake_redis, mock_bybit):
    engine = StopLossEngine(fake_redis, mock_bybit)
    mock_bybit.get_open_orders.return_value = [
        {"stopOrderType": "StopLoss", "triggerPrice": "58000"},
    ]

    price = await engine._fetch_stop_price("BTCUSDT")
    assert price == 58000.0


@pytest.mark.asyncio
async def test_fetch_stop_price_no_orders_returns_zero(fake_redis, mock_bybit):
    engine = StopLossEngine(fake_redis, mock_bybit)
    mock_bybit.get_open_orders.return_value = []

    price = await engine._fetch_stop_price("BTCUSDT")
    assert price == 0.0


@pytest.mark.asyncio
async def test_fetch_stop_price_handles_exception(fake_redis, mock_bybit):
    engine = StopLossEngine(fake_redis, mock_bybit)
    mock_bybit.get_open_orders.side_effect = Exception("timeout")

    price = await engine._fetch_stop_price("BTCUSDT")
    assert price == 0.0


# ---------------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_sets_running_false(fake_redis, mock_bybit):
    engine = StopLossEngine(fake_redis, mock_bybit)
    engine._running = True

    await engine.stop()
    assert engine._running is False


# ---------------------------------------------------------------------------
# _execute_close uses SOR when provided
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_close_uses_sor_when_provided(fake_redis, mock_bybit):
    mock_sor = AsyncMock()
    mock_sor.execute.return_value = {"orderId": "sor_close_001"}

    engine = StopLossEngine(fake_redis, mock_bybit, sor=mock_sor)

    mock_bybit.place_order.return_value = {"orderId": "direct_001"}

    await engine._execute_close("BTCUSDT", "Buy", 0.5, 58900.0, 59000.0)

    # SOR should be used, not direct bybit
    mock_sor.execute.assert_called_once()
    mock_bybit.place_order.assert_not_called()
