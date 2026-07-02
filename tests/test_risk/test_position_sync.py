"""Tests for PositionReconciler — phantom/missing/size-drift detection."""

import pytest
from unittest.mock import AsyncMock
from decimal import Decimal

from src.risk.position_sync import PositionReconciler, SIZE_DRIFT_TOLERANCE_PCT


@pytest.fixture
def bybit():
    client = AsyncMock()
    client._http_client = AsyncMock()
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
