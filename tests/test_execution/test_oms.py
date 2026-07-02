"""Tests for src/execution/oms.py — OrderManagementSystem."""

import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.execution.oms import (
    ORDER_STALE_SEC,
    REDIS_ORDER_PREFIX,
    REDIS_ORDER_SET,
    VALID_TRANSITIONS,
    OrderManagementSystem,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_order(order_id="o1", ticker="BTCUSDT", side="Buy", qty=1.0,
                order_type="Limit", status="SUBMITTED", created_at=None):
    return {
        "order_id": order_id,
        "ticker": ticker,
        "side": side,
        "quantity": qty,
        "order_type": order_type,
        "status": status,
        "filled_qty": 0,
        "avg_fill_price": 0,
        "created_at": created_at or int(time.time()),
        "updated_at": int(time.time()),
    }


# ---------------------------------------------------------------------------
# track_order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_track_order_stores_in_redis(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    result = await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    assert result["order_id"] == "o1"
    assert result["ticker"] == "BTCUSDT"
    assert result["side"] == "Buy"
    assert result["quantity"] == 0.5
    assert result["order_type"] == "Limit"
    assert result["status"] == "SUBMITTED"
    assert result["filled_qty"] == 0

    # Verify persisted in Redis
    stored = json.loads(await fake_redis.get(f"{REDIS_ORDER_PREFIX}:o1"))
    assert stored["order_id"] == "o1"
    assert stored["status"] == "SUBMITTED"

    # Verify in active set
    members = await fake_redis.smembers(REDIS_ORDER_SET)
    assert "o1" in members


@pytest.mark.asyncio
async def test_track_order_with_kwargs(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    result = await oms.track_order("o2", "ETHUSDT", "Sell", 1.0, "Market",
                                   entry_price=3500.0, signal_id="sig_99")
    assert result["entry_price"] == 3500.0
    assert result["signal_id"] == "sig_99"


# ---------------------------------------------------------------------------
# update_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_status_valid_transition(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    ok = await oms.update_status("o1", "FILLED", filled_qty=0.5, avg_price=60000.0)
    assert ok is True

    stored = json.loads(await fake_redis.get(f"{REDIS_ORDER_PREFIX}:o1"))
    assert stored["status"] == "FILLED"
    assert stored["filled_qty"] == 0.5
    assert stored["avg_fill_price"] == 60000.0


@pytest.mark.asyncio
async def test_update_status_terminal_removes_from_active(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    # FILLED is terminal — should remove from active set
    await oms.update_status("o1", "FILLED")
    members = await fake_redis.smembers(REDIS_ORDER_SET)
    assert "o1" not in members


@pytest.mark.asyncio
async def test_update_status_cancelled_removes_from_active(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    await oms.update_status("o1", "CANCELLED")
    members = await fake_redis.smembers(REDIS_ORDER_SET)
    assert "o1" not in members


@pytest.mark.asyncio
async def test_update_status_rejected_removes_from_active(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    await oms.update_status("o1", "REJECTED")
    members = await fake_redis.smembers(REDIS_ORDER_SET)
    assert "o1" not in members


@pytest.mark.asyncio
async def test_update_status_partial_keeps_in_active(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 1.0, "Limit")

    ok = await oms.update_status("o1", "PARTIAL", filled_qty=0.3, avg_price=60000.0)
    assert ok is True

    members = await fake_redis.smembers(REDIS_ORDER_SET)
    assert "o1" in members


@pytest.mark.asyncio
async def test_update_status_invalid_transition_rejected(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    # SUBMITTED -> FILLED is fine, but FILLED -> CANCELLED is invalid
    await oms.update_status("o1", "FILLED")
    ok = await oms.update_status("o1", "CANCELLED")
    assert ok is False


@pytest.mark.asyncio
async def test_update_status_not_found_returns_false(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    ok = await oms.update_status("nonexistent", "FILLED")
    assert ok is False


@pytest.mark.asyncio
async def test_update_status_same_status_allowed(fake_redis, mock_bybit):
    """Re-setting the same status should succeed (no transition check)."""
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    ok = await oms.update_status("o1", "SUBMITTED")
    assert ok is True


@pytest.mark.asyncio
async def test_update_status_partial_to_partial_allowed(fake_redis, mock_bybit):
    """PARTIAL -> PARTIAL is valid for additional partial fills."""
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 1.0, "Limit")

    await oms.update_status("o1", "PARTIAL", filled_qty=0.3)
    ok = await oms.update_status("o1", "PARTIAL", filled_qty=0.6)
    assert ok is True

    stored = json.loads(await fake_redis.get(f"{REDIS_ORDER_PREFIX}:o1"))
    assert stored["filled_qty"] == 0.6


# ---------------------------------------------------------------------------
# get_order / get_active_orders
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_order_found(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    order = await oms.get_order("o1")
    assert order is not None
    assert order["order_id"] == "o1"


@pytest.mark.asyncio
async def test_get_order_not_found(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    order = await oms.get_order("missing")
    assert order is None


@pytest.mark.asyncio
async def test_get_active_orders(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")
    await oms.track_order("o2", "ETHUSDT", "Sell", 1.0, "Market")
    await oms.track_order("o3", "SOLUSDT", "Buy", 10.0, "Limit")

    # Fill o2 — should be removed from active
    await oms.update_status("o2", "FILLED")

    active = await oms.get_active_orders()
    active_ids = {o["order_id"] for o in active}
    assert "o1" in active_ids
    assert "o2" not in active_ids
    assert "o3" in active_ids


@pytest.mark.asyncio
async def test_get_active_orders_cleans_orphaned_set_member(fake_redis, mock_bybit):
    """If an order_id is in the set but the key expired, it should be pruned."""
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    # Manually delete the order key (simulates TTL expiry)
    await fake_redis.delete(f"{REDIS_ORDER_PREFIX}:o1")
    # But the set member remains
    await fake_redis.sadd(REDIS_ORDER_SET, "o1")

    active = await oms.get_active_orders()
    active_ids = {o["order_id"] for o in active}
    assert "o1" not in active_ids


# ---------------------------------------------------------------------------
# cleanup_stuck_orders
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_stuck_orders_cancels_stale_limit(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)

    # Create an order with old timestamp (20 minutes ago)
    old_ts = int(time.time()) - 20 * 60
    await oms.track_order("stale1", "BTCUSDT", "Buy", 0.5, "Limit")
    # Overwrite with old timestamp
    order = _make_order(order_id="stale1", order_type="Limit", created_at=old_ts)
    await fake_redis.setex(f"{REDIS_ORDER_PREFIX}:stale1", 3600, json.dumps(order))

    stuck = await oms.cleanup_stuck_orders()
    assert len(stuck) == 1
    assert stuck[0]["order_id"] == "stale1"

    # Should have called cancel_order
    mock_bybit.cancel_order.assert_called_once()

    # Should now be CANCELLED
    stored = json.loads(await fake_redis.get(f"{REDIS_ORDER_PREFIX}:stale1"))
    assert stored["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_cleanup_stuck_orders_ignores_fresh_orders(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("fresh1", "BTCUSDT", "Buy", 0.5, "Limit")

    stuck = await oms.cleanup_stuck_orders()
    assert len(stuck) == 0
    mock_bybit.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_stuck_orders_ignores_market_orders(fake_redis, mock_bybit):
    """Market orders should not be cancelled as stuck."""
    oms = OrderManagementSystem(fake_redis, mock_bybit)

    old_ts = int(time.time()) - 20 * 60
    await oms.track_order("mkt1", "BTCUSDT", "Buy", 0.5, "Market")
    order = _make_order(order_id="mkt1", order_type="Market", created_at=old_ts)
    await fake_redis.setex(f"{REDIS_ORDER_PREFIX}:mkt1", 3600, json.dumps(order))

    stuck = await oms.cleanup_stuck_orders()
    assert len(stuck) == 0


@pytest.mark.asyncio
async def test_cleanup_stuck_orders_handles_cancel_error(fake_redis, mock_bybit):
    """If cancel_order raises, the error is caught and other orders still processed."""
    oms = OrderManagementSystem(fake_redis, mock_bybit)

    old_ts = int(time.time()) - 20 * 60
    await oms.track_order("err1", "BTCUSDT", "Buy", 0.5, "Limit")
    order = _make_order(order_id="err1", order_type="Limit", created_at=old_ts)
    await fake_redis.setex(f"{REDIS_ORDER_PREFIX}:err1", 3600, json.dumps(order))

    mock_bybit.cancel_order.side_effect = Exception("Bybit connection error")

    stuck = await oms.cleanup_stuck_orders()
    # Error is swallowed — stuck list should be empty for this order
    assert len(stuck) == 0


@pytest.mark.asyncio
async def test_cleanup_stuck_orders_ignores_limit_maker(fake_redis, mock_bybit):
    """limit_maker should also be eligible for stuck cleanup."""
    oms = OrderManagementSystem(fake_redis, mock_bybit)

    old_ts = int(time.time()) - 20 * 60
    await oms.track_order("lm1", "BTCUSDT", "Buy", 0.5, "limit_maker")
    order = _make_order(order_id="lm1", order_type="limit_maker", created_at=old_ts)
    await fake_redis.setex(f"{REDIS_ORDER_PREFIX}:lm1", 3600, json.dumps(order))

    stuck = await oms.cleanup_stuck_orders()
    assert len(stuck) == 1


# ---------------------------------------------------------------------------
# sync_from_exchange
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_from_exchange_updates_filled(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    mock_bybit.get_order_status = AsyncMock(return_value={
        "status": "Filled",
        "filled_qty": 0.5,
        "avg_price": 60000.0,
    })

    await oms.sync_from_exchange()

    stored = json.loads(await fake_redis.get(f"{REDIS_ORDER_PREFIX}:o1"))
    assert stored["status"] == "FILLED"
    assert stored["filled_qty"] == 0.5
    assert stored["avg_fill_price"] == 60000.0


@pytest.mark.asyncio
async def test_sync_from_exchange_maps_cancelled(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    mock_bybit.get_order_status = AsyncMock(return_value={
        "status": "Cancelled",
        "filled_qty": 0,
        "avg_price": 0,
    })

    await oms.sync_from_exchange()
    stored = json.loads(await fake_redis.get(f"{REDIS_ORDER_PREFIX}:o1"))
    assert stored["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_sync_from_exchange_maps_partially_filled(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 1.0, "Limit")

    mock_bybit.get_order_status = AsyncMock(return_value={
        "status": "PartiallyFilled",
        "filled_qty": 0.3,
        "avg_price": 60000.0,
    })

    await oms.sync_from_exchange()
    stored = json.loads(await fake_redis.get(f"{REDIS_ORDER_PREFIX}:o1"))
    assert stored["status"] == "PARTIAL"


@pytest.mark.asyncio
async def test_sync_from_exchange_handles_error_response(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    mock_bybit.get_order_status = AsyncMock(return_value={
        "error": "Order not found",
    })

    # Should not crash — just skip this order
    await oms.sync_from_exchange()


@pytest.mark.asyncio
async def test_sync_from_exchange_handles_exception(fake_redis, mock_bybit):
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    mock_bybit.get_order_status.side_effect = Exception("Network error")

    # Should not crash
    await oms.sync_from_exchange()


@pytest.mark.asyncio
async def test_sync_from_exchange_skips_unchanged_status(fake_redis, mock_bybit):
    """If exchange status matches local, no update needed."""
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    await oms.track_order("o1", "BTCUSDT", "Buy", 0.5, "Limit")

    mock_bybit.get_order_status = AsyncMock(return_value={
        "status": "New",
        "filled_qty": 0,
        "avg_price": 0,
    })

    await oms.sync_from_exchange()
    # Status was SUBMITTED, exchange says "New" which maps to SUBMITTED — same, no update
    stored = json.loads(await fake_redis.get(f"{REDIS_ORDER_PREFIX}:o1"))
    assert stored["status"] == "SUBMITTED"


# ---------------------------------------------------------------------------
# Valid transitions sanity
# ---------------------------------------------------------------------------

def test_valid_transitions_cover_all_states():
    """Verify the transition map covers the expected states."""
    assert "NEW" in VALID_TRANSITIONS
    assert "SUBMITTED" in VALID_TRANSITIONS
    assert "PARTIAL" in VALID_TRANSITIONS

    # Terminal states should NOT be in the source keys
    assert "FILLED" not in VALID_TRANSITIONS
    assert "CANCELLED" not in VALID_TRANSITIONS
    assert "REJECTED" not in VALID_TRANSITIONS


def test_valid_transitions_new_leads_to_submitted():
    assert "SUBMITTED" in VALID_TRANSITIONS["NEW"]


def test_valid_transitions_submitted_can_be_filled():
    assert "FILLED" in VALID_TRANSITIONS["SUBMITTED"]


def test_valid_transitions_partial_can_be_filled():
    assert "FILLED" in VALID_TRANSITIONS["PARTIAL"]
