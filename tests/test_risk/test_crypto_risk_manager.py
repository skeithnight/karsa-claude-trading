"""Tests for CryptoRiskManager — kill switch, liquidation proximity, correlation limits."""

import pytest
from unittest.mock import AsyncMock, patch

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
            {"symbol": "BTCUSDT", "entry_price": 65000, "size": 0.01},
            {"symbol": "ETHUSDT", "entry_price": 3500, "size": 0.1},
            {"symbol": "BTCUSDT", "entry_price": 65000, "size": 0.01},
        ]
        result = risk_mgr.check_correlation_limits("BTCUSDT", positions, 10000)
        assert result["allowed"] is False

    def test_tier3_single(self, risk_mgr):
        positions = [
            {"symbol": "DOGEUSDT", "entry_price": 0.15, "size": 1000},
            {"symbol": "XRPUSDT", "entry_price": 0.15, "size": 1000},
            {"symbol": "ADAUSDT", "entry_price": 0.15, "size": 1000},
            {"symbol": "DOTUSDT", "entry_price": 0.15, "size": 1000},
            {"symbol": "MATICUSDT", "entry_price": 0.15, "size": 1000},
        ]
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


# ---------------------------------------------------------------------------
# New test cases below — appended, existing tests above are untouched
# ---------------------------------------------------------------------------


class TestMaxConcurrentPositionsReject:
    @pytest.mark.asyncio
    async def test_reject_when_at_limit(self, risk_mgr):
        """Gate 2: Max concurrent positions (default 5) — evaluate rejects when
        open_positions already has >= max_concurrent entries."""
        max_concurrent = risk_mgr.max_concurrent  # 5
        open_positions = [
            {"symbol": f"POS{i}USDT", "entry_price": 100, "size": 1}
            for i in range(max_concurrent)
        ]
        signal = {"ticker": "BTCUSDT", "direction": "LONG", "confidence_score": 75, "entry_price": 65000.0}
        result = await risk_mgr.evaluate(signal=signal, open_positions=open_positions, wallet_balance=10000.0)
        assert result["approved"] is False
        assert "concurrent" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_approve_when_under_limit(self, risk_mgr):
        """One slot left — should proceed past concurrent gate."""
        max_concurrent = risk_mgr.max_concurrent  # 5
        open_positions = [
            {"symbol": f"POS{i}USDT", "entry_price": 100, "size": 1}
            for i in range(max_concurrent - 1)
        ]
        signal = {"ticker": "BTCUSDT", "direction": "LONG", "confidence_score": 75, "entry_price": 65000.0}
        result = await risk_mgr.evaluate(signal=signal, open_positions=open_positions, wallet_balance=10000.0)
        assert result["approved"] is True


class TestDailyLossLimitReject:
    @pytest.mark.asyncio
    async def test_reject_at_limit(self, risk_mgr):
        """Gate 1: Daily loss limit (default 3%) — evaluate rejects when
        daily_pnl_pct reaches the negative threshold."""
        signal = {"ticker": "BTCUSDT", "direction": "LONG", "confidence_score": 75, "entry_price": 65000.0}
        result = await risk_mgr.evaluate(
            signal=signal, open_positions=[], wallet_balance=10000.0, daily_pnl_pct=-3.0
        )
        assert result["approved"] is False
        assert "daily loss" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_reject_past_limit(self, risk_mgr):
        """Worse than limit — also rejected."""
        signal = {"ticker": "BTCUSDT", "direction": "LONG", "confidence_score": 75, "entry_price": 65000.0}
        result = await risk_mgr.evaluate(
            signal=signal, open_positions=[], wallet_balance=10000.0, daily_pnl_pct=-5.0
        )
        assert result["approved"] is False

    @pytest.mark.asyncio
    async def test_approve_within_limit(self, risk_mgr):
        """Loss under the limit — allowed."""
        signal = {"ticker": "BTCUSDT", "direction": "LONG", "confidence_score": 75, "entry_price": 65000.0}
        result = await risk_mgr.evaluate(
            signal=signal, open_positions=[], wallet_balance=10000.0, daily_pnl_pct=-2.5
        )
        assert result["approved"] is True


class TestPositionSizeCap:
    @pytest.mark.asyncio
    async def test_size_capped_at_max_position_pct(self, risk_mgr):
        """Gate 5: When the computed position value exceeds max_position_pct (10%)
        of wallet_balance, evaluate should cap the quantity down rather than reject.
        With a $10,000 wallet the cap is $1,000 notional."""
        signal = {"ticker": "BTCUSDT", "direction": "LONG", "confidence_score": 75, "entry_price": 65000.0}
        result = await risk_mgr.evaluate(signal=signal, open_positions=[], wallet_balance=10000.0)
        assert result["approved"] is True
        # position_value should not exceed 10% of 10000 = 1000
        assert result["position_value"] <= 1000.0 + 0.01  # small float tolerance
        # qty * entry_price == position_value (within rounding)
        assert abs(result["qty"] * 65000.0 - result["position_value"]) < 1.0


class TestShortDirection:
    @pytest.mark.asyncio
    async def test_short_approves(self, risk_mgr):
        """SHORT direction should pass direction validation and evaluate normally."""
        risk_mgr.mcp.get_funding_rate = AsyncMock(return_value={"funding_rate": 0.0001})
        signal = {"ticker": "BTCUSDT", "direction": "SHORT", "confidence_score": 75, "entry_price": 65000.0}
        result = await risk_mgr.evaluate(signal=signal, open_positions=[], wallet_balance=10000.0)
        assert result["approved"] is True
        # For SHORT, take_profit should be below entry_price
        assert result["take_profit"] < 65000.0
        # stop_loss should be above entry_price for SHORT
        assert result["stop_loss"] > 65000.0


class TestCloseDirection:
    @pytest.mark.asyncio
    async def test_close_rejected_by_direction_validation(self, risk_mgr):
        """Gate 0: CLOSE is not in the (LONG, SHORT) direction validation set
        and should be rejected with 'Invalid direction'."""
        signal = {"ticker": "BTCUSDT", "direction": "CLOSE", "confidence_score": 75, "entry_price": 65000.0}
        result = await risk_mgr.evaluate(signal=signal, open_positions=[], wallet_balance=10000.0)
        assert result["approved"] is False
        assert "invalid direction" in result["reason"].lower()
        assert "close" in result["reason"].lower()


class TestLiquidationProximityExtended:
    """Extended tests for check_liquidation_proximity() — SHORT force_close and threshold math."""

    def test_safe_long(self, risk_mgr):
        """Long well above liquidation price — safe level."""
        pos = {
            "side": "Buy",
            "entry_price": 60000,
            "liquidation_price": 50000,
            "current_price": 68000,
        }
        result = risk_mgr.check_liquidation_proximity(pos)
        assert result["level"] == "safe"
        assert result["should_close"] is False
        # distance = (68000 - 50000) / 60000 * 100 = 30%
        assert result["distance_pct"] == pytest.approx(30.0, abs=0.1)

    def test_warning_long(self, risk_mgr):
        """Long within warning range (<= 20% from liq price)."""
        pos = {
            "side": "Buy",
            "entry_price": 60000,
            "liquidation_price": 50000,
            "current_price": 58000,
        }
        result = risk_mgr.check_liquidation_proximity(pos)
        assert result["level"] == "warning"
        assert result["should_close"] is False
        # distance = (58000 - 50000) / 60000 * 100 = 13.33%
        assert result["distance_pct"] == pytest.approx(13.33, abs=0.1)

    def test_danger_long(self, risk_mgr):
        """Long within danger/alert range (<= 10% from liq price)."""
        pos = {
            "side": "Buy",
            "entry_price": 60000,
            "liquidation_price": 50000,
            "current_price": 55000,
        }
        result = risk_mgr.check_liquidation_proximity(pos)
        assert result["level"] == "danger"
        assert result["should_close"] is False
        # distance = (55000 - 50000) / 60000 * 100 = 8.33%
        assert result["distance_pct"] == pytest.approx(8.33, abs=0.1)

    def test_force_close_long(self, risk_mgr):
        """Long within force_close range (<= 5% from liq price)."""
        pos = {
            "side": "Buy",
            "entry_price": 60000,
            "liquidation_price": 50000,
            "current_price": 52000,
        }
        result = risk_mgr.check_liquidation_proximity(pos)
        assert result["level"] == "force_close"
        assert result["should_close"] is True
        # distance = (52000 - 50000) / 60000 * 100 = 3.33%
        assert result["distance_pct"] == pytest.approx(3.33, abs=0.1)

    def test_safe_short(self, risk_mgr):
        """Short well below liquidation price — warning level (18.75% < 20% warn threshold).
        For SHORT (Sell side): distance = (liq_price - current_price) / entry * 100"""
        pos = {
            "side": "Sell",
            "entry_price": 80000,
            "liquidation_price": 100000,
            "current_price": 85000,
        }
        result = risk_mgr.check_liquidation_proximity(pos)
        # distance = (100000 - 85000) / 80000 * 100 = 18.75%
        # 18.75% <= 20% (warn threshold) -> "warning"
        assert result["level"] == "warning"
        assert result["should_close"] is False
        assert result["distance_pct"] == pytest.approx(18.75, abs=0.1)

    def test_force_close_short(self, risk_mgr):
        """Short near liquidation — force_close.
        For SHORT: distance = (liq_price - current_price) / entry * 100"""
        pos = {
            "side": "Sell",
            "entry_price": 80000,
            "liquidation_price": 100000,
            "current_price": 98000,
        }
        result = risk_mgr.check_liquidation_proximity(pos)
        assert result["level"] == "force_close"
        assert result["should_close"] is True
        # distance = (100000 - 98000) / 80000 * 100 = 2.5%
        assert result["distance_pct"] == pytest.approx(2.5, abs=0.1)

    def test_missing_prices_returns_unknown(self, risk_mgr):
        """Missing liquidation_price or current_price — returns 'unknown'."""
        pos = {"side": "Buy", "entry_price": 60000, "liquidation_price": None, "current_price": 55000}
        result = risk_mgr.check_liquidation_proximity(pos)
        assert result["level"] == "unknown"
        assert result["should_close"] is False


class TestCooldownRedis:
    @pytest.mark.asyncio
    async def test_reject_when_cooldown_active(self, risk_mgr):
        """Gate 4: When Redis karsa:crypto_cooldown key exists, evaluate rejects."""
        risk_mgr._redis.get = AsyncMock(return_value="active")
        signal = {"ticker": "BTCUSDT", "direction": "LONG", "confidence_score": 75, "entry_price": 65000.0}
        result = await risk_mgr.evaluate(signal=signal, open_positions=[], wallet_balance=10000.0)
        assert result["approved"] is False
        assert "cooldown" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_approve_when_no_cooldown(self, risk_mgr):
        """No cooldown key — should not trigger cooldown gate."""
        risk_mgr._redis.get = AsyncMock(return_value=None)
        signal = {"ticker": "BTCUSDT", "direction": "LONG", "confidence_score": 75, "entry_price": 65000.0}
        result = await risk_mgr.evaluate(signal=signal, open_positions=[], wallet_balance=10000.0)
        assert result["approved"] is True


class TestPositionSizingWithATR:
    @pytest.mark.asyncio
    async def test_atr_based_sizing(self, risk_mgr):
        """Position sizing uses 1.5x ATR (default sl_mult) as stop distance when no
        stop_loss_price is provided. OHLCV data is fetched from MCP."""
        ohlcv = [
            {"high": 66000, "low": 64000, "close": 65000},
            {"high": 66500, "low": 64500, "close": 65500},
            {"high": 67000, "low": 65000, "close": 66000},
        ]
        risk_mgr.mcp.get_ohlcv = AsyncMock(return_value=ohlcv)
        signal = {"ticker": "BTCUSDT", "direction": "LONG", "confidence_score": 75, "entry_price": 65000.0}
        result = await risk_mgr.evaluate(signal=signal, open_positions=[], wallet_balance=10000.0)
        assert result["approved"] is True
        assert result["qty"] > 0
        assert result["stop_loss"] > 0
        assert result["take_profit"] > 0

        # Verify ATR calculation: True ranges = max(H-L, |H-prevC|, |L-prevC|)
        tr1 = max(66500 - 64500, abs(66500 - 65000), abs(64500 - 65000))  # 2000
        tr2 = max(67000 - 65000, abs(67000 - 65500), abs(65000 - 65500))  # 2000
        expected_atr = (tr1 + tr2) / 2  # 2000
        expected_stop_distance = expected_atr * 1.5  # 3000 (sl_mult=1.5)
        expected_stop_loss = 65000.0 - expected_stop_distance  # 62000

        # max_position_value = 10000 * 0.10 = 1000
        # uncapped qty = 100 / 3000 = 0.03333
        # uncapped value = 0.03333 * 65000 = 2166.67 > 1000 -> capped
        # capped qty = 1000 / 65000 = 0.01538
        expected_qty = round(1000.0 / 65000.0, 6)
        expected_tp = 65000.0 + (expected_atr * 3.0)  # 71000

        assert result["qty"] == pytest.approx(expected_qty, abs=1e-4)
        assert result["stop_loss"] == pytest.approx(expected_stop_loss, abs=1.0)
        assert result["take_profit"] == pytest.approx(expected_tp, abs=1.0)


class TestCheckCorrelationLimitsAllTiers:
    """check_correlation_limits() — tier1 single allowed, tier2 combined exposure at limit,
    tier3 max positions, unknown symbol defaults to tier3."""

    def test_tier1_single_allowed(self, risk_mgr):
        """Tier1 has max_positions=5, so one existing position still allows another."""
        positions = [{"symbol": "BTCUSDT", "entry_price": 65000, "size": 0.01}]
        result = risk_mgr.check_correlation_limits("ETHUSDT", positions, 100000)
        assert result["allowed"] is True
        assert result["tier"] == "tier1"
        assert result["max_leverage"] == 10

    def test_tier2_combined_exposure_at_limit(self, risk_mgr):
        """Tier2 max_combined_pct=0.25 (25%). One position at 27% exposure
        should block a new tier2 entry."""
        positions = [
            {"symbol": "SOLUSDT", "entry_price": 150, "size": 1800},   # 270000, 270000/1000000 = 27%
        ]
        # total_exposure = 270000, 270000/1000000 = 27% >= 25%
        result = risk_mgr.check_correlation_limits("AVAXUSDT", positions, 1000000)
        assert result["allowed"] is False
        assert result["tier"] == "tier2"
        assert "exposure" in result["reason"].lower()

    def test_tier3_max_positions(self, risk_mgr):
        """Tier3 max_positions=5. Five existing tier3 positions block another tier3 entry."""
        positions = [
            {"symbol": "DOGEUSDT", "entry_price": 0.15, "size": 1000},
            {"symbol": "XRPUSDT", "entry_price": 0.15, "size": 1000},
            {"symbol": "ADAUSDT", "entry_price": 0.15, "size": 1000},
            {"symbol": "DOTUSDT", "entry_price": 0.15, "size": 1000},
            {"symbol": "MATICUSDT", "entry_price": 0.15, "size": 1000},
        ]
        result = risk_mgr.check_correlation_limits("XRPUSDT", positions, 10000)
        assert result["allowed"] is False
        assert result["tier"] == "tier3"
        assert "max 5 positions" in result["reason"]

    def test_unknown_symbol_defaults_to_tier3(self, risk_mgr):
        """A symbol not in any tier defaults to tier3 and uses tier3 limits."""
        positions = [
            {"symbol": "DOGEUSDT", "entry_price": 0.15, "size": 1000},
            {"symbol": "XRPUSDT", "entry_price": 0.15, "size": 1000},
            {"symbol": "ADAUSDT", "entry_price": 0.15, "size": 1000},
            {"symbol": "DOTUSDT", "entry_price": 0.15, "size": 1000},
            {"symbol": "MATICUSDT", "entry_price": 0.15, "size": 1000},
        ]
        # UNKNOWNUSDT is not in any tier -> defaults to tier3
        result = risk_mgr.check_correlation_limits("UNKNOWNUSDT", positions, 10000)
        # tier3 already has 5 positions (max_positions=5), so blocked
        assert result["allowed"] is False
        assert result["tier"] == "tier3"

    def test_unknown_no_conflict(self, risk_mgr):
        """Unknown symbol with no conflicting positions — allowed with tier3 leverage cap."""
        result = risk_mgr.check_correlation_limits("UNKNOWNUSDT", [], 10000)
        assert result["allowed"] is True
        assert result["tier"] == "tier3"
        assert result["max_leverage"] == 3


class TestMissingEntryPrice:
    @pytest.mark.asyncio
    async def test_reject_zero_entry_price(self, risk_mgr):
        """Gate 0: entry_price=0 should reject with 'Missing or invalid entry price'."""
        signal = {"ticker": "BTCUSDT", "direction": "LONG", "confidence_score": 75, "entry_price": 0}
        result = await risk_mgr.evaluate(signal=signal, open_positions=[], wallet_balance=10000.0)
        assert result["approved"] is False
        assert "entry price" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_reject_negative_entry_price(self, risk_mgr):
        """Negative entry_price also rejected."""
        signal = {"ticker": "BTCUSDT", "direction": "LONG", "confidence_score": 75, "entry_price": -100}
        result = await risk_mgr.evaluate(signal=signal, open_positions=[], wallet_balance=10000.0)
        assert result["approved"] is False
        assert "entry price" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_reject_missing_entry_price(self, risk_mgr):
        """entry_price absent from signal dict — defaults to 0 -> rejected."""
        signal = {"ticker": "BTCUSDT", "direction": "LONG", "confidence_score": 75}
        result = await risk_mgr.evaluate(signal=signal, open_positions=[], wallet_balance=10000.0)
        assert result["approved"] is False
        assert "entry price" in result["reason"].lower()


class TestCheckKillSwitch:
    @pytest.mark.asyncio
    async def test_redis_emergency_active(self, risk_mgr, mock_bybit):
        """When Redis emergency stop is active, check_kill_switch triggers."""
        import src.risk.emergency as emergency_mod
        risk_mgr._kill_switch_active = False  # ensure clean state
        risk_mgr._last_kill_check = 0.0       # force interval check
        with patch.object(emergency_mod, "is_active", new_callable=AsyncMock, return_value=True):
            result = await risk_mgr.check_kill_switch(mock_bybit)
            assert result["triggered"] is True
            assert "redis" in result["reason"].lower() or "emergency" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_redis_emergency_inactive_no_trigger(self, risk_mgr, mock_bybit):
        """When Redis emergency stop is inactive and P&L is fine, no trigger."""
        import src.risk.emergency as emergency_mod
        mock_bybit.get_wallet_balance = AsyncMock(return_value={"balance": 10000.0})
        mock_bybit.get_positions = AsyncMock(return_value=[
            {"unrealized_pnl": -100.0}
        ])
        with patch.object(emergency_mod, "is_active", new_callable=AsyncMock, return_value=False):
            risk_mgr._last_kill_check = 0.0
            result = await risk_mgr.check_kill_switch(mock_bybit)
            assert result["triggered"] is False
            assert result["loss_pct"] < risk_mgr.daily_loss_limit * 100

    @pytest.mark.asyncio
    async def test_account_pnl_triggers_kill(self, risk_mgr, mock_bybit):
        """When account unrealized loss exceeds daily_loss_limit (3%), kill switch activates."""
        import src.risk.emergency as emergency_mod
        # loss_pct = abs(-400) / 10000 * 100 = 4.0% > 3.0% limit
        mock_bybit.get_wallet_balance = AsyncMock(return_value={"balance": 10000.0})
        mock_bybit.get_positions = AsyncMock(return_value=[
            {"unrealized_pnl": -400.0}
        ])
        with patch.object(emergency_mod, "is_active", new_callable=AsyncMock, return_value=False):
            risk_mgr._last_kill_check = 0.0
            risk_mgr._kill_switch_active = False  # reset
            result = await risk_mgr.check_kill_switch(mock_bybit)
            assert result["triggered"] is True
            assert "daily loss" in result["reason"].lower() or "exceeds" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_account_pnl_within_limit(self, risk_mgr, mock_bybit):
        """Loss under limit — no trigger."""
        import src.risk.emergency as emergency_mod
        # loss_pct = abs(-200) / 10000 * 100 = 2.0% < 3.0% limit
        mock_bybit.get_wallet_balance = AsyncMock(return_value={"balance": 10000.0})
        mock_bybit.get_positions = AsyncMock(return_value=[
            {"unrealized_pnl": -200.0}
        ])
        with patch.object(emergency_mod, "is_active", new_callable=AsyncMock, return_value=False):
            risk_mgr._last_kill_check = 0.0
            risk_mgr._kill_switch_active = False
            result = await risk_mgr.check_kill_switch(mock_bybit)
            assert result["triggered"] is False


class TestRejectHelper:
    def test_reject_shape(self, risk_mgr):
        """_reject returns dict with approved=False, reason, and zeroed quantities."""
        result = risk_mgr._reject("test reason")
        assert result["approved"] is False
        assert result["reason"] == "test reason"
        assert result["qty"] == 0
        assert result["stop_loss"] == 0
        assert result["take_profit"] == 0
        assert result["risk_amount"] == 0
        assert result["leverage"] == 1

    def test_reject_preserves_reason(self, risk_mgr):
        """Reason string is passed through verbatim."""
        reason = "Daily loss limit breached: -3.50%"
        result = risk_mgr._reject(reason)
        assert result["reason"] == reason

    def test_reject_contains_required_keys(self, risk_mgr):
        """All required keys present for downstream consumers."""
        result = risk_mgr._reject("any reason")
        required_keys = {"approved", "reason", "qty", "stop_loss", "take_profit", "risk_amount", "leverage"}
        assert required_keys.issubset(result.keys())
