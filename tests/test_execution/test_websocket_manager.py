"""Tests for src/execution/websocket_manager.py — WebSocketManager."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.execution.websocket_manager import (
    REDIS_PRICE_PREFIX,
    REDIS_TICK_CHANNEL,
    SYNC_INTERVAL_SEC,
    WebSocketManager,
)


# ---------------------------------------------------------------------------
# _handle_tick
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_tick_stores_price_in_redis(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    manager._subscribed.add("BTCUSDT")

    message = {
        "data": [{
            "lastPrice": "60000.50",
            "bid1Price": "60000.00",
            "ask1Price": "60001.00",
            "volume24h": "12345.67",
        }],
    }

    await manager._handle_tick("BTCUSDT", message)

    stored = await fake_redis.get(f"{REDIS_PRICE_PREFIX}:BTCUSDT")
    assert stored is not None
    data = json.loads(stored)
    assert data["ticker"] == "BTCUSDT"
    assert data["price"] == 60000.50
    assert data["bid"] == 60000.00
    assert data["ask"] == 60001.00
    assert data["volume_24h"] == 12345.67


@pytest.mark.asyncio
async def test_handle_tick_publishes_to_channel(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    manager._subscribed.add("BTCUSDT")

    message = {"data": [{"lastPrice": "60000.50"}]}

    await manager._handle_tick("BTCUSDT", message)

    assert len(fake_redis._published) == 1
    channel, payload = fake_redis._published[0]
    assert channel == REDIS_TICK_CHANNEL
    data = json.loads(payload)
    assert data["ticker"] == "BTCUSDT"
    assert data["price"] == 60000.50


@pytest.mark.asyncio
async def test_handle_tick_ignores_unsubscribed_ticker(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    # BTCUSDT is NOT in _subscribed
    message = {"data": [{"lastPrice": "60000.50"}]}

    await manager._handle_tick("BTCUSDT", message)

    stored = await fake_redis.get(f"{REDIS_PRICE_PREFIX}:BTCUSDT")
    assert stored is None


@pytest.mark.asyncio
async def test_handle_tick_ignores_empty_data(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    manager._subscribed.add("BTCUSDT")

    await manager._handle_tick("BTCUSDT", {"data": []})
    await manager._handle_tick("BTCUSDT", {"data": None})
    await manager._handle_tick("BTCUSDT", {})

    stored = await fake_redis.get(f"{REDIS_PRICE_PREFIX}:BTCUSDT")
    assert stored is None


@pytest.mark.asyncio
async def test_handle_tick_ignores_missing_price(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    manager._subscribed.add("BTCUSDT")

    message = {"data": [{"bid1Price": "60000.00"}]}  # no lastPrice

    await manager._handle_tick("BTCUSDT", message)

    stored = await fake_redis.get(f"{REDIS_PRICE_PREFIX}:BTCUSDT")
    assert stored is None


@pytest.mark.asyncio
async def test_handle_tick_accepts_dict_data_not_list(fake_redis, mock_bybit):
    """Some WS messages may have data as dict instead of list."""
    manager = WebSocketManager(fake_redis, mock_bybit)
    manager._subscribed.add("ETHUSDT")

    message = {
        "data": {
            "lastPrice": "3500.00",
            "bid1Price": "3499.00",
            "ask1Price": "3501.00",
        },
    }

    await manager._handle_tick("ETHUSDT", message)

    stored = await fake_redis.get(f"{REDIS_PRICE_PREFIX}:ETHUSDT")
    assert stored is not None
    data = json.loads(stored)
    assert data["price"] == 3500.00


@pytest.mark.asyncio
async def test_handle_tick_uses_last_price_camelcase_and_snake(fake_redis, mock_bybit):
    """Both camelCase and snake_case field names should be handled."""
    manager = WebSocketManager(fake_redis, mock_bybit)
    manager._subscribed.add("SOLUSDT")

    # snake_case variant
    message = {"data": [{"last_price": "150.00"}]}
    await manager._handle_tick("SOLUSDT", message)

    stored = await fake_redis.get(f"{REDIS_PRICE_PREFIX}:SOLUSDT")
    data = json.loads(stored)
    assert data["price"] == 150.00


@pytest.mark.asyncio
async def test_handle_tick_exception_does_not_propagate(fake_redis, mock_bybit):
    """Errors during tick handling should be swallowed."""
    manager = WebSocketManager(fake_redis, mock_bybit)
    manager._subscribed.add("BTCUSDT")

    fake_redis.setex = AsyncMock(side_effect=Exception("Redis down"))

    # Should not raise
    await manager._handle_tick("BTCUSDT", {"data": [{"lastPrice": "60000"}]})


# ---------------------------------------------------------------------------
# get_realtime_price
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_realtime_price_returns_cached_price(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)

    await fake_redis.setex(
        f"{REDIS_PRICE_PREFIX}:BTCUSDT", 5,
        json.dumps({"price": 60000.0, "ticker": "BTCUSDT"}),
    )

    price = await manager.get_realtime_price("BTCUSDT")
    assert price == 60000.0


@pytest.mark.asyncio
async def test_get_realtime_price_returns_none_when_missing(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)

    price = await manager.get_realtime_price("UNKNOWN")
    assert price is None


@pytest.mark.asyncio
async def test_get_realtime_price_handles_malformed_json(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    await fake_redis.setex(f"{REDIS_PRICE_PREFIX}:BTCUSDT", 5, "not-json")

    price = await manager.get_realtime_price("BTCUSDT")
    assert price is None


# ---------------------------------------------------------------------------
# _sync_subscriptions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_subscribes_to_new_positions(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    mock_bybit.get_positions.return_value = [
        {"symbol": "BTCUSDT", "size": 0.5, "side": "Buy"},
    ]

    with patch.object(manager, "_subscribe_ticker", new_callable=AsyncMock) as mock_sub:
        await manager._sync_subscriptions()
        mock_sub.assert_called_once_with("BTCUSDT")


@pytest.mark.asyncio
async def test_sync_unsubscribes_closed_positions(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    manager._subscribed = {"BTCUSDT", "ETHUSDT"}

    mock_bybit.get_positions.return_value = [
        {"symbol": "BTCUSDT", "size": 0.5, "side": "Buy"},
    ]
    mock_bybit.get_open_orders.return_value = []

    with patch.object(manager, "_unsubscribe_ticker", new_callable=AsyncMock) as mock_unsub:
        with patch.object(manager, "_subscribe_ticker", new_callable=AsyncMock):
            await manager._sync_subscriptions()
            mock_unsub.assert_called_once_with("ETHUSDT")


@pytest.mark.asyncio
async def test_sync_includes_pending_orders(fake_redis, mock_bybit):
    """Pending limit orders should also be subscribed."""
    manager = WebSocketManager(fake_redis, mock_bybit)

    mock_bybit.get_positions.return_value = [
        {"symbol": "BTCUSDT", "size": 0.5, "side": "Buy"},
    ]
    mock_bybit.get_open_orders.return_value = [
        {"symbol": "SOLUSDT"},
    ]

    with patch.object(manager, "_subscribe_ticker", new_callable=AsyncMock) as mock_sub:
        await manager._sync_subscriptions()
        subscribed_tickers = {call.args[0] for call in mock_sub.call_args_list}
        assert "BTCUSDT" in subscribed_tickers
        assert "SOLUSDT" in subscribed_tickers


@pytest.mark.asyncio
async def test_sync_handles_none_positions(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    mock_bybit.get_positions.return_value = None

    # Should not crash
    await manager._sync_subscriptions()


@pytest.mark.asyncio
async def test_sync_skips_zero_size_positions(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    mock_bybit.get_positions.return_value = [
        {"symbol": "BTCUSDT", "size": 0, "side": "Buy"},
    ]

    with patch.object(manager, "_subscribe_ticker", new_callable=AsyncMock) as mock_sub:
        await manager._sync_subscriptions()
        mock_sub.assert_not_called()


@pytest.mark.asyncio
async def test_sync_handles_exception(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    mock_bybit.get_positions.side_effect = Exception("Bybit down")

    # Should not crash
    await manager._sync_subscriptions()


# ---------------------------------------------------------------------------
# _subscribe_ticker / _unsubscribe_ticker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unsubscribe_removes_from_cache(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    manager._subscribed.add("BTCUSDT")

    await manager._unsubscribe_ticker("BTCUSDT")

    assert "BTCUSDT" not in manager._subscribed


@pytest.mark.asyncio
async def test_unsubscribe_deletes_redis_key(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    manager._subscribed.add("BTCUSDT")
    await fake_redis.setex(f"{REDIS_PRICE_PREFIX}:BTCUSDT", 5, "{}")

    await manager._unsubscribe_ticker("BTCUSDT")

    stored = await fake_redis.get(f"{REDIS_PRICE_PREFIX}:BTCUSDT")
    assert stored is None


@pytest.mark.asyncio
async def test_unsubscribe_idempotent(fake_redis, mock_bybit):
    """Unsubscribing a ticker not in the set should not raise."""
    manager = WebSocketManager(fake_redis, mock_bybit)
    # NOT in _subscribed
    await manager._unsubscribe_ticker("UNKNOWN")
    assert "UNKNOWN" not in manager._subscribed


# ---------------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_sets_running_false(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    manager._running = True

    await manager.stop()
    assert manager._running is False


@pytest.mark.asyncio
async def test_stop_exits_websocket(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    manager._ws = MagicMock()

    await manager.stop()
    manager._ws.exit.assert_called_once()


@pytest.mark.asyncio
async def test_stop_handles_no_websocket(fake_redis, mock_bybit):
    manager = WebSocketManager(fake_redis, mock_bybit)
    manager._ws = None

    # Should not crash
    await manager.stop()
    assert manager._running is False
