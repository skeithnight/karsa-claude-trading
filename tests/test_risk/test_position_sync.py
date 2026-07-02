"""Tests for PositionReconciler — phantom/missing/size-drift detection."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from decimal import Decimal

from src.risk.position_sync import (
    PositionReconciler, OrderReconciler, BalanceReconciler,
    SIZE_DRIFT_TOLERANCE_PCT, BALANCE_DRIFT_TOLERANCE_PCT,
)


@pytest.fixture
def bybit():
    client = AsyncMock()
    client._http_client = MagicMock()
    return client


@pytest.fixture
def reconciler(bybit):
    return PositionReconciler(bybit)


class TestGetExchangePositions:
    @pytest.mark.asyncio
    async def test_returns_positions(self, reconciler, bybit):
        bybit._http_client.get_positions.return_value = {
            "retCode": 0,
            "result": {"list": [
                {"symbol": "BTCUSDT", "size": "0.01", "avgPrice": "65000", "side": "Buy"},
                {"symbol": "ETHUSDT", "size": "0.1", "avgPrice": "3500", "side": "Sell"},
            ]},
        }
        result = await reconciler._get_exchange_positions()
        assert len(result) == 2
        assert result[0]["symbol"] == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_filters_zero_size(self, reconciler, bybit):
        bybit._http_client.get_positions.return_value = {
            "retCode": 0,
            "result": {"list": [
                {"symbol": "BTCUSDT", "size": "0.01", "avgPrice": "65000"},
                {"symbol": "ETHUSDT", "size": "0", "avgPrice": "0"},
            ]},
        }
        result = await reconciler._get_exchange_positions()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_api_error(self, reconciler, bybit):
        bybit._http_client.get_positions.return_value = {"retCode": 10001, "result": {"list": []}}
        result = await reconciler._get_exchange_positions()
        assert result == []


class TestSizeDriftTolerance:
    def test_tolerance_value(self):
        assert SIZE_DRIFT_TOLERANCE_PCT == Decimal("1.0")


# ---- helpers ----

def _make_position(id, ticker, status="OPEN", size="0.01", side="Buy", order_id=None):
    return SimpleNamespace(
        id=id, ticker=ticker, status=status,
        size=Decimal(size), side=side,
        entry_price=Decimal("65000"),
        current_price=Decimal("65000"),
        unrealized_pnl=Decimal("0"),
        order_id=order_id,
        created_at=None,
    )


def _make_exch_position(symbol, size="0.01", side="Buy", avgPrice="65000"):
    return {
        "symbol": symbol, "size": size, "side": side,
        "avgPrice": avgPrice, "markPrice": avgPrice,
        "leverage": "10", "marginMode": "isolated",
        "liqPrice": "60000", "unrealisedPnl": "0",
    }


# ---- additional fixtures ----

@pytest.fixture
def order_reconciler(bybit):
    return OrderReconciler(bybit)


@pytest.fixture
def balance_reconciler(bybit):
    return BalanceReconciler(bybit)


@pytest.fixture
def mock_db():
    """Mock async_session for DB writes in reconciliation handlers."""
    with patch('src.risk.position_sync.async_session') as mock_factory:
        session = AsyncMock()
        ctx = AsyncMock()
        ctx.__aenter__.return_value = session
        mock_factory.return_value = ctx
        yield session


# ---- PositionReconciler: reconcile() ----

class TestFullReconcile:
    @pytest.mark.asyncio
    async def test_mixed_phantom_missing_size_drift(self, reconciler, mock_db):
        """reconcile() returns all three drift types when DB has phantom + drift and exchange has missing."""
        db_positions = [
            _make_position(id=1, ticker="BTCUSDT", status="OPEN", size="0.01"),
            _make_position(id=2, ticker="ETHUSDT", status="OPEN", size="0.1"),
        ]
        exchange_positions = [
            _make_exch_position(symbol="ETHUSDT", size="0.2", side="Buy"),
            _make_exch_position(symbol="SOLUSDT", size="10", side="Buy"),
        ]

        with patch.object(reconciler, '_get_db_positions', return_value=db_positions), \
             patch.object(reconciler, '_get_exchange_positions', return_value=exchange_positions):
            drifts = await reconciler.reconcile()

        drift_types = {d["drift_type"] for d in drifts}
        assert drift_types == {"PHANTOM", "MISSING", "SIZE_DRIFT"}


class TestPhantomDetection:
    @pytest.mark.asyncio
    async def test_phantom_marked_closed(self, reconciler, mock_db):
        """Position in DB but not on exchange gets marked CLOSED."""
        db_positions = [
            _make_position(id=1, ticker="BTCUSDT", status="OPEN", size="0.01"),
        ]

        with patch.object(reconciler, '_get_db_positions', return_value=db_positions), \
             patch.object(reconciler, '_get_exchange_positions', return_value=[]):
            drifts = await reconciler.reconcile()

        assert len(drifts) == 1
        assert drifts[0]["drift_type"] == "PHANTOM"
        assert drifts[0]["ticker"] == "BTCUSDT"
        assert drifts[0]["resolution"] == "marked_closed"


class TestMissingDetection:
    @pytest.mark.asyncio
    async def test_missing_logged(self, reconciler, mock_db):
        """Position on exchange but not in DB gets logged."""
        exchange_positions = [
            _make_exch_position(symbol="SOLUSDT", size="10", side="Buy"),
        ]

        with patch.object(reconciler, '_get_db_positions', return_value=[]), \
             patch.object(reconciler, '_get_exchange_positions', return_value=exchange_positions):
            drifts = await reconciler.reconcile()

        assert len(drifts) == 1
        assert drifts[0]["drift_type"] == "MISSING"
        assert drifts[0]["ticker"] == "SOLUSDT"
        assert drifts[0]["resolution"] == "created_from_exchange"


class TestSizeDriftDetection:
    @pytest.mark.asyncio
    async def test_size_drift_detected(self, reconciler, mock_db):
        """Size mismatch beyond tolerance gets detected."""
        db_positions = [
            _make_position(id=1, ticker="BTCUSDT", status="OPEN", size="0.1"),
        ]
        exchange_positions = [
            _make_exch_position(symbol="BTCUSDT", size="0.2"),
        ]

        with patch.object(reconciler, '_get_db_positions', return_value=db_positions), \
             patch.object(reconciler, '_get_exchange_positions', return_value=exchange_positions):
            drifts = await reconciler.reconcile()

        assert len(drifts) == 1
        assert drifts[0]["drift_type"] == "SIZE_DRIFT"
        assert drifts[0]["ticker"] == "BTCUSDT"
        assert drifts[0]["db_size"] == "0.1"
        assert drifts[0]["exchange_size"] == "0.2"
        assert drifts[0]["resolution"] == "updated_to_exchange"

    @pytest.mark.asyncio
    async def test_size_drift_within_tolerance_ignored(self, reconciler, mock_db):
        """Size mismatch within 1% tolerance is ignored."""
        db_positions = [
            _make_position(id=1, ticker="BTCUSDT", status="OPEN", size="0.1"),
        ]
        # 0.1005 vs 0.1 = 0.5% drift, within 1% tolerance
        exchange_positions = [
            _make_exch_position(symbol="BTCUSDT", size="0.1005"),
        ]

        with patch.object(reconciler, '_get_db_positions', return_value=db_positions), \
             patch.object(reconciler, '_get_exchange_positions', return_value=exchange_positions):
            drifts = await reconciler.reconcile()

        assert drifts == []


# ---- OrderReconciler: reconcile_orders() ----

class TestOrderReconciliation:
    @pytest.mark.asyncio
    async def test_orphaned_and_unknown(self, order_reconciler, mock_db):
        """Detects both orphaned and unknown orders."""
        db_orders = [
            _make_position(id=1, ticker="BTCUSDT", status="PENDING", order_id="ORD1"),
            _make_position(id=2, ticker="ETHUSDT", status="PENDING", order_id="ORD2"),
        ]
        exchange_orders = [
            {"orderId": "ORD2", "symbol": "ETHUSDT", "side": "Buy", "qty": "0.1", "price": "3500"},
            {"orderId": "ORD3", "symbol": "SOLUSDT", "side": "Sell", "qty": "5", "price": "150"},
        ]

        with patch.object(order_reconciler, '_get_db_orders', return_value=db_orders), \
             patch.object(order_reconciler, '_get_exchange_orders', return_value=exchange_orders):
            drifts = await order_reconciler.reconcile_orders()

        drift_types = [d["drift_type"] for d in drifts]
        assert "ORPHANED" in drift_types
        assert "UNKNOWN" in drift_types

    @pytest.mark.asyncio
    async def test_orphaned_order(self, order_reconciler, mock_db):
        """Order in DB but not on exchange gets detected."""
        db_orders = [
            _make_position(id=1, ticker="BTCUSDT", status="PENDING", order_id="ORD1"),
        ]

        with patch.object(order_reconciler, '_get_db_orders', return_value=db_orders), \
             patch.object(order_reconciler, '_get_exchange_orders', return_value=[]):
            drifts = await order_reconciler.reconcile_orders()

        assert len(drifts) == 1
        assert drifts[0]["drift_type"] == "ORPHANED"
        assert drifts[0]["order_id"] == "ORD1"
        assert drifts[0]["resolution"] == "closed"

    @pytest.mark.asyncio
    async def test_unknown_order(self, order_reconciler, mock_db):
        """Order on exchange but not in DB gets detected."""
        exchange_orders = [
            {"orderId": "ORD_NEW", "symbol": "SOLUSDT", "side": "Buy", "qty": "10", "price": "150"},
        ]

        with patch.object(order_reconciler, '_get_db_orders', return_value=[]), \
             patch.object(order_reconciler, '_get_exchange_orders', return_value=exchange_orders):
            drifts = await order_reconciler.reconcile_orders()

        assert len(drifts) == 1
        assert drifts[0]["drift_type"] == "UNKNOWN"
        assert drifts[0]["ticker"] == "SOLUSDT"
        assert drifts[0]["order_id"] == "ORD_NEW"
        assert drifts[0]["resolution"] == "logged"


# ---- BalanceReconciler: reconcile_balances() ----

class TestBalanceReconciliation:
    @pytest.mark.asyncio
    async def test_balance_drift_detected(self, balance_reconciler, mock_db):
        """Balance drift beyond tolerance gets detected."""
        with patch.object(balance_reconciler, '_get_exchange_balance',
                          return_value={"USDT": Decimal("2000")}), \
             patch.object(balance_reconciler, '_get_db_balance',
                          return_value={"USDT": Decimal("1000")}):
            drifts = await balance_reconciler.reconcile_balances()

        assert len(drifts) == 1
        assert drifts[0]["drift_type"] == "BALANCE"
        assert drifts[0]["currency"] == "USDT"
        assert drifts[0]["db_balance"] == "1000"
        assert drifts[0]["exchange_balance"] == "2000"
        assert drifts[0]["resolution"] == "exchange_trusted"

    @pytest.mark.asyncio
    async def test_balance_within_tolerance(self, balance_reconciler, mock_db):
        """Balance drift within 0.01% is ignored."""
        # drift_pct = |1000 - 1000.001| / max(1000, 1000.001) * 100 ~ 0.0001% < 0.01%
        with patch.object(balance_reconciler, '_get_exchange_balance',
                          return_value={"USDT": Decimal("1000.001")}), \
             patch.object(balance_reconciler, '_get_db_balance',
                          return_value={"USDT": Decimal("1000")}):
            drifts = await balance_reconciler.reconcile_balances()

        assert drifts == []


# ---- Exception handling ----

class TestReconcileExceptionHandling:
    @pytest.mark.asyncio
    async def test_exchange_positions_raises_returns_empty(self, reconciler):
        """When _get_exchange_positions raises, reconcile returns empty list."""
        with patch.object(reconciler, '_get_exchange_positions', side_effect=Exception("API down")):
            drifts = await reconciler.reconcile()

        assert drifts == []
