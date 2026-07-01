"""Tests for CryptoRiskManager — kill switch, liquidation proximity, correlation limits."""

import pytest
from unittest.mock import AsyncMock

from src.risk.crypto_risk_manager import (
    CryptoRiskManager, MAX_LEVERAGE_BY_TIER, _get_tier
)


@pytest.fixture
def risk_mgr():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    return CryptoRiskManager(mcp=AsyncMock(), redis_client=mock_redis)


@pytest.fixture
def mock_bybit():
    client = AsyncMock()
    client.get_wallet_balance.return_value = {"balance": 10000.0, "available": 8000.0}
    client.get_positions.return_value = []
    return client


class TestCorrelationTiers:
    def test_btc_tier1(self): assert _get_tier("BTCUSDT") == "tier1"
    def test_sol_tier2(self): assert _get_tier("SOLUSDT") == "tier2"
    def test_doge_tier3(self): assert _get_tier("DOGEUSDT") == "tier3"
    def test_unknown_tier3(self): assert _get_tier("UNKNOWNUSDT") == "tier3"
    def test_tier1_max_leverage(self): assert MAX_LEVERAGE_BY_TIER["tier1"] == 10
    def test_tier3_max_leverage(self): assert MAX_LEVERAGE_BY_TIER["tier3"] == 3


class TestKillSwitch:
    def test_initially_inactive(self, risk_mgr):
        assert not risk_mgr.is_kill_switch_active()

    def test_activate_deactivate(self, risk_mgr):
        risk_mgr.activate_kill_switch("test")
        assert risk_mgr.is_kill_switch_active()
        risk_mgr.deactivate_kill_switch()
        assert not risk_mgr.is_kill_switch_active()

    @pytest.mark.asyncio
    async def test_triggers_on_large_loss(self, risk_mgr, mock_bybit):
        mock_bybit.get_positions.return_value = [{"unrealized_pnl": -400.0}]
        risk_mgr._last_kill_check = 0
        result = await risk_mgr.check_kill_switch(mock_bybit)
        assert result["triggered"] is True

    @pytest.mark.asyncio
    async def test_no_trigger_small_loss(self, risk_mgr, mock_bybit):
        mock_bybit.get_positions.return_value = [{"unrealized_pnl": -50.0}]
        risk_mgr._last_kill_check = 0
        result = await risk_mgr.check_kill_switch(mock_bybit)
        assert result["triggered"] is False

    @pytest.mark.asyncio
    async def test_uses_balance_key(self, risk_mgr, mock_bybit):
        mock_bybit.get_wallet_balance.return_value = {"balance": 5000.0}
        mock_bybit.get_positions.return_value = [{"unrealized_pnl": -200.0}]
        risk_mgr._last_kill_check = 0
        result = await risk_mgr.check_kill_switch(mock_bybit)
        assert result["triggered"] is True
        assert result["account_value"] == 5000.0


class TestLiquidationProximity:
    def test_safe(self, risk_mgr):
        pos = {"liquidation_price": 50000, "current_price": 65000, "entry_price": 65000, "side": "Buy"}
        assert risk_mgr.check_liquidation_proximity(pos)["level"] == "safe"

    def test_warning(self, risk_mgr):
        pos = {"liquidation_price": 55250, "current_price": 65000, "entry_price": 65000, "side": "Buy"}
        assert risk_mgr.check_liquidation_proximity(pos)["level"] == "warning"

    def test_danger(self, risk_mgr):
        pos = {"liquidation_price": 59800, "current_price": 65000, "entry_price": 65000, "side": "Buy"}
        assert risk_mgr.check_liquidation_proximity(pos)["level"] == "danger"

    def test_force_close(self, risk_mgr):
        pos = {"liquidation_price": 63050, "current_price": 65000, "entry_price": 65000, "side": "Buy"}
        result = risk_mgr.check_liquidation_proximity(pos)
        assert result["level"] == "force_close"
        assert result["should_close"] is True

    def test_short_safe(self, risk_mgr):
        pos = {"liquidation_price": 80000, "current_price": 65000, "entry_price": 65000, "side": "Sell"}
        assert risk_mgr.check_liquidation_proximity(pos)["level"] == "safe"

    def test_missing_data(self, risk_mgr):
        pos = {"liquidation_price": None, "current_price": 65000, "entry_price": 65000, "side": "Buy"}
        assert risk_mgr.check_liquidation_proximity(pos)["level"] == "unknown"


class TestCorrelationLimits:
    def test_within_tier1(self, risk_mgr):
        positions = [{"symbol": "BTCUSDT", "entry_price": 65000, "size": 0.01}]
        result = risk_mgr.check_correlation_limits("ETHUSDT", positions, 10000)
        assert result["allowed"] is True

    def test_tier1_max_positions(self, risk_mgr):
        positions = [
            {"symbol": "BTCUSDT", "entry_price": 65000, "size": 0.01},
            {"symbol": "ETHUSDT", "entry_price": 3500, "size": 0.1},
        ]
        result = risk_mgr.check_correlation_limits("BTCUSDT", positions, 10000)
        assert result["allowed"] is False

    def test_tier3_single(self, risk_mgr):
        positions = [{"symbol": "DOGEUSDT", "entry_price": 0.15, "size": 1000}]
        result = risk_mgr.check_correlation_limits("XRPUSDT", positions, 10000)
        assert result["allowed"] is False


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_approve_valid(self, risk_mgr):
        signal = {"ticker": "BTCUSDT", "direction": "LONG", "confidence_score": 75, "entry_price": 65000.0}
        result = await risk_mgr.evaluate(signal=signal, open_positions=[], wallet_balance=10000.0)
        assert result["approved"] is True

    @pytest.mark.asyncio
    async def test_reject_low_confidence(self, risk_mgr):
        signal = {"ticker": "ETHUSDT", "direction": "LONG", "confidence_score": 40, "entry_price": 3500.0}
        result = await risk_mgr.evaluate(signal=signal, open_positions=[], wallet_balance=10000.0)
        assert result["approved"] is False
