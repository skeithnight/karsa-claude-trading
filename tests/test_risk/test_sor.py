import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.risk.sor import SmartOrderRouter

@pytest.fixture
def mock_bybit():
    bybit = AsyncMock()
    # Mock methods that are used in sor
    bybit._http_client = MagicMock()
    bybit._http_client.set_leverage = MagicMock()
    bybit.get_orderbook.return_value = {"bids": [["50000", "1"]], "asks": [["50010", "1"]]}
    bybit.place_order.return_value = {"order_id": "test_order", "retCode": 0}
    bybit.get_order_status.return_value = {"status": "Cancelled"}  # Simulates order NOT filling
    bybit.cancel_order.return_value = {"retCode": 0}
    bybit.get_ticker.return_value = {"price": 50000.0}
    bybit.get_positions.return_value = []
    bybit.set_stop_loss.return_value = {"retCode": 0, "order_id": "sl_1"}
    bybit.set_take_profit.return_value = {"retCode": 0, "order_id": "tp_1"}
    return bybit

@pytest.mark.asyncio
async def test_sor_limit_order_not_filling_falls_back_to_market(mock_bybit, monkeypatch):
    """When limit orders don't fill after MAX_REPRICE_ATTEMPTS, falls back to market order."""
    # Simulate: limit order placed but never fills (status=Cancelled after timeout)
    mock_bybit.get_order_status.side_effect = [
        {"status": "Cancelled"},   # limit attempt 1
        {"status": "Cancelled"},   # limit attempt 2 (reprice)
        {"status": "Cancelled"},   # limit attempt 3 (reprice)
        {"status": "Filled", "avg_price": 50000.0},  # market fallback fills
    ]
    mock_bybit.cancel_order.return_value = {"retCode": 0}
    # Market order succeeds
    mock_bybit.place_order.side_effect = [
        {"order_id": "limit_1", "retCode": 0},  # limit attempt 1
        {"order_id": "limit_2", "retCode": 0},  # limit attempt 2 (reprice)
        {"order_id": "limit_3", "retCode": 0},  # limit attempt 3 (reprice)
        {"order_id": "market_1", "retCode": 0},  # market fallback
    ]

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    sor = SmartOrderRouter(mock_bybit)

    signal = {"ticker": "BTCUSDT", "direction": "LONG"}
    risk_params = {"qty": 1.0, "stop_loss": 49000, "take_profit": 55000, "leverage": 1}

    with patch('src.risk.sor.settings.TRADING_MODE', 'live'):
        result = await sor.execute_order(signal, risk_params)

    # Should succeed via market fallback
    assert result["success"] is True
    assert result["fill_price"] == 50000.0


# ---------------------------------------------------------------------------
# Test 1: Zero qty
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_execute_order_zero_qty(mock_bybit):
    """execute_order with qty=0 should return error immediately."""
    sor = SmartOrderRouter(mock_bybit)
    signal = {"ticker": "BTCUSDT", "direction": "LONG"}
    risk_params = {"qty": 0, "stop_loss": 49000, "take_profit": 55000}

    result = await sor.execute_order(signal, risk_params)

    assert result["success"] is False
    assert result["error"] == "Zero quantity"
    mock_bybit.place_order.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Leverage setting
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("src.risk.sor.record_order_fill")
@patch("src.risk.sor.record_slippage")
@patch("src.risk.sor.record_fill_latency")
@patch("src.risk.sor.record_order_rejected")
async def test_execute_order_sets_leverage(mock_rejected, mock_latency, mock_slippage, mock_fill, mock_bybit, monkeypatch):
    """set_leverage must be called before order placement."""
    mock_bybit.get_order_status.return_value = {"status": "Filled", "avg_price": 50000.0, "cumExecQty": "0.5"}
    sor = SmartOrderRouter(mock_bybit)

    signal = {"ticker": "ETHUSDT", "direction": "SHORT"}
    risk_params = {"qty": 0.5, "stop_loss": 51000, "take_profit": 45000, "leverage": 5}

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with patch("src.risk.sor.settings.TRADING_MODE", "live"):
        result = await sor.execute_order(signal, risk_params)

    mock_bybit._http_client.set_leverage.assert_called_once()
    call_args = mock_bybit._http_client.set_leverage.call_args
    assert call_args[1]["symbol"] == "ETHUSDT" or call_args[0][0] == "ETHUSDT"


# ---------------------------------------------------------------------------
# Test 3: Limit order fill
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("src.risk.sor.record_order_fill")
@patch("src.risk.sor.record_slippage")
@patch("src.risk.sor.record_fill_latency")
@patch("src.risk.sor.record_order_rejected")
async def test_execute_order_limit_fill_success(mock_rejected, mock_latency, mock_slippage, mock_fill, mock_bybit, monkeypatch):
    """When limit order fills within timeout, return success with fill_price."""
    mock_bybit.place_order.return_value = {"order_id": "limit_1", "retCode": 0}
    mock_bybit.get_order_status.return_value = {"status": "Filled", "avg_price": 49990.0, "cumExecQty": "1.0"}
    mock_bybit.set_stop_loss.return_value = {"retCode": 0}
    mock_bybit.set_take_profit.return_value = {"retCode": 0}
    sor = SmartOrderRouter(mock_bybit)

    signal = {"ticker": "BTCUSDT", "direction": "LONG"}
    risk_params = {"qty": 1.0, "stop_loss": 49000, "take_profit": 55000, "leverage": 1}

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with patch("src.risk.sor.settings.TRADING_MODE", "live"):
        result = await sor.execute_order(signal, risk_params)

    assert result["success"] is True
    assert result["fill_price"] == 49990.0
    assert result["order_id"] == "limit_1"
    assert result["qty"] == 1.0
    mock_bybit.set_stop_loss.assert_called_once()
    mock_bybit.set_take_profit.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: Reprice on limit failure
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("src.risk.sor.record_order_fill")
@patch("src.risk.sor.record_slippage")
@patch("src.risk.sor.record_fill_latency")
@patch("src.risk.sor.record_order_rejected")
async def test_execute_order_reprice_on_limit_failure(mock_rejected, mock_latency, mock_slippage, mock_fill, mock_bybit, monkeypatch):
    """When first limit attempt fails (not 'too late'), should retry with new price."""
    mock_bybit.place_order.side_effect = [
        {"order_id": "lim_1", "retCode": 0},   # first limit attempt
        {"order_id": "lim_2", "retCode": 0},   # second limit attempt
    ]
    mock_bybit.get_order_status.side_effect = [
        {"status": "Cancelled"},               # first order cancelled
        {"status": "Filled", "avg_price": 50000.0, "cumExecQty": "1.0"},  # second order filled
    ]
    mock_bybit.set_stop_loss.return_value = {"retCode": 0}
    mock_bybit.set_take_profit.return_value = {"retCode": 0}
    sor = SmartOrderRouter(mock_bybit)

    signal = {"ticker": "BTCUSDT", "direction": "LONG"}
    risk_params = {"qty": 1.0, "stop_loss": 49000, "take_profit": 55000, "leverage": 1}

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with patch("src.risk.sor.settings.TRADING_MODE", "live"), \
         patch("src.risk.sor.MAX_REPRICE_ATTEMPTS", 3):
        result = await sor.execute_order(signal, risk_params)

    assert result["success"] is True
    assert result["fill_price"] == 50000.0
    assert result["order_id"] == "lim_2"
    # place_order called twice: first limit attempt + second limit attempt
    assert mock_bybit.place_order.call_count == 2
    mock_bybit.set_stop_loss.assert_called_once()
    mock_bybit.set_take_profit.assert_called_once()


# ---------------------------------------------------------------------------
# Test 5: Market fallback
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("src.risk.sor.record_order_fill")
@patch("src.risk.sor.record_slippage")
@patch("src.risk.sor.record_fill_latency")
@patch("src.risk.sor.record_order_rejected")
async def test_execute_order_market_fallback(mock_rejected, mock_latency, mock_slippage, mock_fill, mock_bybit, monkeypatch):
    """When orderbook has error, should fall back to market order."""
    mock_bybit.get_orderbook.return_value = {"error": "orderbook unavailable"}
    mock_bybit.place_order.return_value = {"order_id": "market_1", "retCode": 0}
    mock_bybit.get_order_status.return_value = {"status": "Filled", "avg_price": 50000.0, "cumExecQty": "1.0"}
    mock_bybit.set_stop_loss.return_value = {"retCode": 0}
    mock_bybit.set_take_profit.return_value = {"retCode": 0}
    sor = SmartOrderRouter(mock_bybit)

    signal = {"ticker": "BTCUSDT", "direction": "LONG"}
    risk_params = {"qty": 1.0, "stop_loss": 49000, "take_profit": 55000, "leverage": 1}

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with patch("src.risk.sor.settings.TRADING_MODE", "live"):
        result = await sor.execute_order(signal, risk_params)

    assert result["success"] is True
    assert result["fill_price"] == 50000.0
    assert result["order_id"] == "market_1"
    # Verify market order was placed
    place_calls = mock_bybit.place_order.call_args_list
    assert len(place_calls) == 1
    assert place_calls[0].kwargs.get("order_type") == "Market"


# ---------------------------------------------------------------------------
# Test 6: Market order failure
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("src.risk.sor.record_order_rejected")
@patch("src.risk.sor.record_fill_latency")
@patch("src.risk.sor.record_slippage")
@patch("src.risk.sor.record_order_fill")
async def test_execute_order_market_order_failure(mock_fill, mock_slippage, mock_latency, mock_rejected, mock_bybit, monkeypatch):
    """When market order returns error, return failure."""
    mock_bybit.get_orderbook.return_value = {"error": "unavailable"}
    mock_bybit.place_order.return_value = {"error": "Insufficient balance", "retCode": 10001}
    sor = SmartOrderRouter(mock_bybit)

    signal = {"ticker": "BTCUSDT", "direction": "LONG"}
    risk_params = {"qty": 1.0, "stop_loss": 49000, "take_profit": 55000}

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with patch("src.risk.sor.settings.TRADING_MODE", "live"):
        result = await sor.execute_order(signal, risk_params)

    assert result["success"] is False
    assert "Insufficient balance" in result["error"]
    mock_bybit.place_order.assert_called_once()


# ---------------------------------------------------------------------------
# Test 7: Market order success
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("src.risk.sor.record_order_fill")
@patch("src.risk.sor.record_slippage")
@patch("src.risk.sor.record_fill_latency")
@patch("src.risk.sor.record_order_rejected")
async def test_execute_order_market_order_success(mock_rejected, mock_latency, mock_slippage, mock_fill, mock_bybit, monkeypatch):
    """When market order succeeds, return success with fill_price."""
    mock_bybit.get_orderbook.return_value = {"error": "unavailable"}
    mock_bybit.place_order.return_value = {"order_id": "market_2", "retCode": 0}
    mock_bybit.get_order_status.return_value = {"status": "Filled", "avg_price": 50015.0, "cumExecQty": "1.0"}
    mock_bybit.set_stop_loss.return_value = {"retCode": 0}
    mock_bybit.set_take_profit.return_value = {"retCode": 0}
    sor = SmartOrderRouter(mock_bybit)

    signal = {"ticker": "BTCUSDT", "direction": "LONG"}
    risk_params = {"qty": 1.0, "stop_loss": 49000, "take_profit": 55000, "leverage": 1}

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with patch("src.risk.sor.settings.TRADING_MODE", "live"):
        result = await sor.execute_order(signal, risk_params)

    assert result["success"] is True
    assert result["fill_price"] == 50015.0
    assert result["order_id"] == "market_2"
    assert result["qty"] == 1.0
    mock_bybit.set_stop_loss.assert_called_once()
    mock_bybit.set_take_profit.assert_called_once()


# ---------------------------------------------------------------------------
# Test 8: close_position with LONG
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_close_position_long(mock_bybit):
    """close_position() with a LONG position should sell to close."""
    mock_bybit.place_order.return_value = {"order_id": "close_1", "retCode": 0}
    sor = SmartOrderRouter(mock_bybit)

    pos = {"symbol": "BTCUSDT", "side": "Buy", "size": 0.5}
    result = await sor.close_position(pos["symbol"], pos)

    assert result["success"] is True
    assert result["order_id"] == "close_1"
    call = mock_bybit.place_order.call_args
    assert call.kwargs["side"] == "Sell"    # LONG position closed by selling
    assert call.kwargs["qty"] == 0.5
    assert call.kwargs["reduce_only"] is True
    assert call.kwargs["order_type"] == "Market"


# ---------------------------------------------------------------------------
# Test 9: flatten_all
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_flatten_all(mock_bybit):
    """flatten_all() closes all open positions."""
    mock_bybit.get_positions.return_value = [
        {"symbol": "BTCUSDT", "side": "Buy", "size": 0.5},
        {"symbol": "ETHUSDT", "side": "Sell", "size": 2.0},
    ]
    mock_bybit.place_order.side_effect = [
        {"order_id": "flat_1", "retCode": 0},
        {"order_id": "flat_2", "retCode": 0},
    ]
    sor = SmartOrderRouter(mock_bybit)

    result = await sor.flatten_all()

    assert result["count"] == 2
    assert "BTCUSDT" in result["closed"]
    assert "ETHUSDT" in result["closed"]
    assert mock_bybit.place_order.call_count == 2
    # Verify Buy position closed with Sell
    calls = mock_bybit.place_order.call_args_list
    assert calls[0].kwargs["side"] == "Sell"
    assert calls[0].kwargs["reduce_only"] is True
    # Verify Sell position closed with Buy
    assert calls[1].kwargs["side"] == "Buy"
    assert calls[1].kwargs["reduce_only"] is True


# ---------------------------------------------------------------------------
# Test 10: flatten_all with failure
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_flatten_all_with_failure(mock_bybit):
    """When one position fails to close, others still get closed."""
    mock_bybit.get_positions.return_value = [
        {"symbol": "BTCUSDT", "side": "Buy", "size": 0.5},
        {"symbol": "ETHUSDT", "side": "Buy", "size": 2.0},
        {"symbol": "SOLUSDT", "side": "Buy", "size": 10.0},
    ]
    mock_bybit.place_order.side_effect = [
        {"order_id": "flat_1", "retCode": 0},       # BTCUSDT succeeds
        {"error": "Insufficient balance", "retCode": 10001},  # ETHUSDT fails
        {"order_id": "flat_3", "retCode": 0},       # SOLUSDT succeeds
    ]
    sor = SmartOrderRouter(mock_bybit)

    result = await sor.flatten_all()

    assert result["count"] == 2
    assert "BTCUSDT" in result["closed"]
    assert "ETHUSDT" not in result["closed"]
    assert "SOLUSDT" in result["closed"]
    assert mock_bybit.place_order.call_count == 3


# ---------------------------------------------------------------------------
# Test 11: Paper mode
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("src.risk.sor.record_order_fill")
@patch("src.risk.sor.record_slippage")
@patch("src.risk.sor.record_fill_latency")
@patch("src.risk.sor.record_order_rejected")
async def test_execute_order_paper_mode(mock_rejected, mock_latency, mock_slippage, mock_fill, mock_bybit, monkeypatch):
    """When TRADING_MODE is 'paper', should return mock fill immediately."""
    mock_bybit.get_ticker = AsyncMock(return_value={"price": 50000.0})
    sor = SmartOrderRouter(mock_bybit)

    signal = {"ticker": "BTCUSDT", "direction": "LONG", "entry_price": 50000}
    risk_params = {"qty": 1.0, "stop_loss": 49000, "take_profit": 55000}

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with patch("src.risk.sor.settings.TRADING_MODE", "paper"):
        result = await sor.execute_order(signal, risk_params)

    assert result["success"] is True
    assert result["fill_price"] == 50000.0
    assert result["qty"] == 1.0
    assert result["order_id"].startswith("paper_")
    # No real order placed
    mock_bybit.place_order.assert_not_called()


# ---------------------------------------------------------------------------
# Test 12: Orderbook error dict triggers market fallback
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("src.risk.sor.record_order_fill")
@patch("src.risk.sor.record_slippage")
@patch("src.risk.sor.record_fill_latency")
@patch("src.risk.sor.record_order_rejected")
async def test_execute_order_orderbook_error_triggers_market(mock_rejected, mock_latency, mock_slippage, mock_fill, mock_bybit, monkeypatch):
    """When orderbook returns error dict (e.g. {'error': '...'}), should trigger market fallback."""
    mock_bybit.get_orderbook.return_value = {"error": "connection timeout"}
    mock_bybit.place_order.return_value = {"order_id": "market_fb", "retCode": 0}
    mock_bybit.get_order_status.return_value = {"status": "Filled", "avg_price": 49950.0, "cumExecQty": "1.0"}
    mock_bybit.set_stop_loss.return_value = {"retCode": 0}
    mock_bybit.set_take_profit.return_value = {"retCode": 0}
    sor = SmartOrderRouter(mock_bybit)

    signal = {"ticker": "BTCUSDT", "direction": "LONG"}
    risk_params = {"qty": 1.0, "stop_loss": 49000, "take_profit": 55000, "leverage": 1}

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with patch("src.risk.sor.settings.TRADING_MODE", "live"):
        result = await sor.execute_order(signal, risk_params)

    assert result["success"] is True
    assert result["fill_price"] == 49950.0
    assert result["order_id"] == "market_fb"
    # Market order was used as fallback
    mock_bybit.place_order.assert_called_once()
    call = mock_bybit.place_order.call_args
    assert call.kwargs["order_type"] == "Market"
    # set_stop_loss and set_take_profit were called
    mock_bybit.set_stop_loss.assert_called_once()
    mock_bybit.set_take_profit.assert_called_once()


# ---------------------------------------------------------------------------
# Test 13: close_position error
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_close_position_error(mock_bybit):
    """When place_order raises exception in close_position, should return failure."""
    mock_bybit.place_order.side_effect = Exception("connection reset")
    sor = SmartOrderRouter(mock_bybit)

    pos = {"symbol": "BTCUSDT", "side": "Buy", "size": 0.5}
    result = await sor.close_position(pos["symbol"], pos)

    assert result["success"] is False
    assert "connection reset" in result["error"]
