"""Tests for FundingTracker — rate fetching, cost calculation, alerts."""

import pytest
from unittest.mock import AsyncMock, patch

from src.risk.funding_tracker import FundingTracker, CRYPTO_UNIVERSE


@pytest.fixture
def bybit():
    return AsyncMock()


@pytest.fixture
def tracker(bybit):
    return FundingTracker(bybit)


class TestCalculatePositionFundingCost:
    def test_positive_rate(self, tracker):
        result = tracker.calculate_position_funding_cost(
            position_value_usdt=10000, funding_rate=0.001, leverage=1
        )
        assert result["payment_usdt"] == 10.0
        assert result["payment_pct"] == 0.1

    def test_with_leverage(self, tracker):
        result = tracker.calculate_position_funding_cost(
            position_value_usdt=10000, funding_rate=0.001, leverage=10
        )
        assert result["payment_usdt"] == 10.0

    def test_negative_rate(self, tracker):
        result = tracker.calculate_position_funding_cost(
            position_value_usdt=10000, funding_rate=-0.0005, leverage=1
        )
        assert result["payment_usdt"] == -5.0

    def test_zero_rate(self, tracker):
        result = tracker.calculate_position_funding_cost(
            position_value_usdt=10000, funding_rate=0, leverage=1
        )
        assert result["payment_usdt"] == 0.0


class TestGetCurrentRates:
    @pytest.mark.asyncio
    async def test_fetches_rates(self, tracker, bybit):
        bybit._http_client.get_tickers.return_value = {
            "retCode": 0,
            "result": {"list": [
                {"symbol": "BTCUSDT", "fundingRate": "0.0001"},
                {"symbol": "ETHUSDT", "fundingRate": "0.0003"},
            ]},
        }
        result = await tracker.get_current_rates()
        assert len(result) > 0
        assert all("symbol" in r for r in result)

    @pytest.mark.asyncio
    async def test_api_error(self, tracker, bybit):
        bybit._http_client.get_tickers.side_effect = Exception("API down")
        result = await tracker.get_current_rates()
        assert isinstance(result, list)


class TestGetAlerts:
    @pytest.mark.asyncio
    async def test_filters_by_alert_flag(self, tracker):
        rates = [
            {"symbol": "BTCUSDT", "funding_rate": 0.001, "alert": True, "funding_cost_pct": 0.1, "annualized_pct": 109.5},
            {"symbol": "ETHUSDT", "funding_rate": 0.0001, "alert": False, "funding_cost_pct": 0.01, "annualized_pct": 10.95},
        ]
        with patch.object(tracker, "get_current_rates", return_value=rates):
            result = await tracker.get_alerts()
        assert len(result) == 1
        assert result[0]["symbol"] == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_enriches_with_position_cost(self, tracker):
        rates = [
            {"symbol": "BTCUSDT", "funding_rate": 0.001, "alert": True, "funding_cost_pct": 0.1, "annualized_pct": 109.5},
        ]
        positions = [{"symbol": "BTCUSDT", "entry_price": 65000, "size": 0.1, "leverage": 5}]
        with patch.object(tracker, "get_current_rates", return_value=rates):
            result = await tracker.get_alerts(positions=positions)
        assert len(result) == 1
        assert result[0]["position_cost"] is not None

    @pytest.mark.asyncio
    async def test_no_alerts(self, tracker):
        rates = [{"symbol": "BTCUSDT", "funding_rate": 0.0001, "alert": False}]
        with patch.object(tracker, "get_current_rates", return_value=rates):
            result = await tracker.get_alerts()
        assert result == []

    @pytest.mark.asyncio
    async def test_no_matching_position(self, tracker):
        rates = [
            {"symbol": "BTCUSDT", "funding_rate": 0.001, "alert": True, "funding_cost_pct": 0.1, "annualized_pct": 109.5},
        ]
        positions = [{"symbol": "ETHUSDT", "entry_price": 3500, "size": 1, "leverage": 5}]
        with patch.object(tracker, "get_current_rates", return_value=rates):
            result = await tracker.get_alerts(positions=positions)
        assert result[0]["position_cost"] is None


class TestSyncFundingFromExchange:
    @pytest.mark.asyncio
    async def test_fetches_history(self, tracker, bybit):
        bybit._http_client.get_funding_history.return_value = {
            "retCode": 0,
            "result": {"list": [
                {"symbol": "BTCUSDT", "funding": "1.5", "fundingRate": "0.0001", "createdTime": "1234567890"},
            ]},
        }
        result = await tracker.sync_funding_from_exchange("BTCUSDT")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_api_error(self, tracker, bybit):
        bybit._http_client.get_funding_history.side_effect = Exception("fail")
        result = await tracker.sync_funding_from_exchange("BTCUSDT")
        assert result == []
