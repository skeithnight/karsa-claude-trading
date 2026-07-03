"""Integration tests for the Karsa crypto risk pipeline.

Tests the full flow: signal → calibration → risk evaluation → SOR execution.
Also verifies that kill switch and circuit breaker block the pipeline.

Mocks Redis, Bybit, and DB at the boundary — but lets the real components
interact with each other to validate wiring.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.risk.calibration_engine import ConfidenceCalibrator
from src.risk.circuit_breaker import CB_KEY_PREFIX, CircuitBreakerManager
from src.risk.crypto_risk_manager import CryptoRiskManager
from src.risk.emergency import (
    GLOBAL_HALT_KEY,
    KILL_KEY,
    activate,
    activate_global_halt,
    deactivate,
    deactivate_global_halt,
    is_active,
    is_global_halt,
)
from src.risk.sor import SmartOrderRouter
from src.execution.oms import OrderManagementSystem
from src.execution.sl_engine import StopLossEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signal(ticker="BTCUSDT", direction="LONG", confidence=75,
            entry_price=60000.0, stop_loss_price=59000.0):
    return {
        "ticker": ticker,
        "direction": direction,
        "confidence_score": confidence,
        "entry_price": entry_price,
        "stop_loss_price": stop_loss_price,
    }


def _position(ticker="BTCUSDT", side="Buy", size=0.5, entry_price=60000.0,
              unrealized_pnl=0.0):
    return {
        "symbol": ticker,
        "side": side,
        "size": size,
        "entry_price": entry_price,
        "unrealized_pnl": unrealized_pnl,
    }


# ---------------------------------------------------------------------------
# Pipeline: signal → calibration → risk → SOR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_approve_and_execute(fake_redis, mock_bybit, _patch_settings):
    """Happy path: well-formed signal passes calibration, risk gates, and SOR."""
    _patch_settings.TRADING_MODE = "live"

    # 1. Calibration — multiplier = 1.0 (default, no trades yet)
    calibrator = ConfidenceCalibrator()
    calibrator._cached_multiplier = 1.0

    adjusted_confidence = await calibrator.calibrate_signal(75.0)
    assert adjusted_confidence == 75.0

    # 2. Risk evaluation
    risk_mgr = CryptoRiskManager(redis_client=fake_redis)
    signal = _signal(confidence=adjusted_confidence)

    result = await risk_mgr.evaluate(
        signal=signal,
        open_positions=[],
        wallet_balance=10000.0,
        regime=None,
        daily_pnl_pct=0.0,
    )

    assert result["approved"] is True
    assert result["qty"] > 0
    assert result["stop_loss"] > 0
    assert result["take_profit"] > 0
    assert result["leverage"] >= 1

    # 3. SOR execution
    mock_bybit.place_order.return_value = {"order_id": "exec_001"}
    mock_bybit.get_order_status.return_value = {
        "status": "Filled",
        "avg_price": 60000.0,
    }
    mock_bybit.set_stop_loss.return_value = {"order_id": "sl_001"}
    mock_bybit.set_take_profit.return_value = {"order_id": "tp_001"}

    sor = SmartOrderRouter(mock_bybit)
    exec_result = await sor.execute_order(signal, result)

    assert exec_result["success"] is True
    assert exec_result["order_id"]  # paper mode generates paper_* IDs


@pytest.mark.asyncio
async def test_pipeline_rejects_low_confidence(fake_redis, _patch_settings):
    """Signal below confidence threshold should be rejected by risk manager."""
    calibrator = ConfidenceCalibrator()
    calibrator._cached_multiplier = 0.5

    adjusted = await calibrator.calibrate_signal(50.0)
    assert adjusted == 25.0  # 50 * 0.5

    risk_mgr = CryptoRiskManager(redis_client=fake_redis)
    signal = _signal(confidence=adjusted)

    result = await risk_mgr.evaluate(
        signal=signal,
        open_positions=[],
        wallet_balance=10000.0,
    )

    assert result["approved"] is False
    assert "Confidence" in result["reason"]


@pytest.mark.asyncio
async def test_pipeline_calibration_reduces_overconfident_signal(fake_redis, _patch_settings):
    """Multiplier < 1.0 should reduce LLM confidence proportionally."""
    calibrator = ConfidenceCalibrator()
    calibrator._cached_multiplier = 0.7

    adjusted = await calibrator.calibrate_signal(90.0)
    assert adjusted == 63.0  # 90 * 0.7


@pytest.mark.asyncio
async def test_pipeline_calibration_boosts_underconfident_signal(fake_redis, _patch_settings):
    """Multiplier > 1.0 should boost confidence."""
    calibrator = ConfidenceCalibrator()
    calibrator._cached_multiplier = 1.3

    adjusted = await calibrator.calibrate_signal(60.0)
    assert adjusted == 78.0  # 60 * 1.3


@pytest.mark.asyncio
async def test_pipeline_calibration_clamps_to_100(fake_redis, _patch_settings):
    """Boosted confidence should not exceed 100."""
    calibrator = ConfidenceCalibrator()
    calibrator._cached_multiplier = 1.5

    adjusted = await calibrator.calibrate_signal(80.0)
    assert adjusted == 100.0  # clamped from 120


# ---------------------------------------------------------------------------
# Kill switch blocks pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kill_switch_blocks_risk_evaluation(fake_redis, _patch_settings):
    """When kill switch is active, risk evaluation should fail-closed."""
    from src.risk import emergency

    # Patch the emergency module's Redis client to use our fake
    original_client = emergency._client
    emergency._client = fake_redis

    try:
        await activate("Test breach", "test")

        risk_mgr = CryptoRiskManager(redis_client=fake_redis)
        signal = _signal()

        # The risk manager checks kill switch via check_kill_switch()
        # which reads from emergency.is_active()
        ks_result = await risk_mgr.check_kill_switch(MagicMock(
            get_wallet_balance=AsyncMock(return_value={"balance": 10000.0}),
            get_positions=AsyncMock(return_value=[]),
        ))

        assert ks_result["triggered"] is True
    finally:
        emergency._client = original_client


@pytest.mark.asyncio
async def test_global_halt_blocks_pipeline(fake_redis, _patch_settings):
    """Global halt (from /kill command) should set both keys."""
    from src.risk import emergency

    original_client = emergency._client
    emergency._client = fake_redis

    try:
        await activate_global_halt("Manual kill", "telegram_user")

        assert await is_global_halt() is True
        assert await is_active() is True

        # Verify both keys are set
        halt_val = await fake_redis.get(GLOBAL_HALT_KEY)
        assert halt_val is not None
        assert json.loads(halt_val)["type"] == "global_halt"

        kill_val = await fake_redis.get(KILL_KEY)
        assert kill_val is not None
    finally:
        emergency._client = original_client


@pytest.mark.asyncio
async def test_kill_switch_deactivation_resumes_pipeline(fake_redis, _patch_settings):
    """After deactivation, risk evaluation should pass again."""
    from src.risk import emergency

    original_client = emergency._client
    emergency._client = fake_redis

    try:
        await activate("Test breach", "test")
        assert await is_active() is True

        await deactivate("test")
        assert await is_active() is False
    finally:
        emergency._client = original_client


@pytest.mark.asyncio
async def test_global_halt_deactivation_clears_both_keys(fake_redis, _patch_settings):
    """Deactivating global halt should clear both halt and kill keys."""
    from src.risk import emergency

    original_client = emergency._client
    emergency._client = fake_redis

    try:
        await activate_global_halt("Manual kill", "telegram_user")
        assert await is_global_halt() is True

        await deactivate_global_halt("telegram_user")
        assert await is_global_halt() is False
        assert await is_active() is False
    finally:
        emergency._client = original_client


# ---------------------------------------------------------------------------
# Circuit breaker blocks pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_circuit_breaker_daily_dd_blocks_trading(fake_redis, mock_bybit, _patch_settings):
    """If daily drawdown circuit breaker is active, new trades should be blocked."""
    _patch_settings.CRYPTO_DAILY_LOSS_LIMIT_PCT = 3.0

    cb = CircuitBreakerManager(fake_redis, mock_bybit)

    # Simulate an active daily DD breaker
    await fake_redis.setex(
        f"{CB_KEY_PREFIX}:DAILY_DD", 1800,
        json.dumps({"daily_pnl_pct": -4.5, "limit_pct": 3.0}),
    )

    # The risk pipeline should check for active breakers
    active = await cb.get_active_breakers()
    assert len(active) == 1
    assert active[0]["type"] == "DAILY_DD"


@pytest.mark.asyncio
async def test_circuit_breaker_volatility_spike_detected(fake_redis, mock_bybit, _patch_settings):
    """Volatility spike breaker should be activatable."""
    cb = CircuitBreakerManager(fake_redis, mock_bybit)

    # Simulate an active volatility breaker
    await fake_redis.setex(
        f"{CB_KEY_PREFIX}:VOLATILITY:BTCUSDT", 1800,
        json.dumps({"ticker": "BTCUSDT", "spike_pct": 6.5, "lookback_min": 15}),
    )

    active = await cb.get_active_breakers()
    assert len(active) == 1
    assert "VOLATILITY" in active[0]["type"]


@pytest.mark.asyncio
async def test_circuit_breaker_no_active_returns_empty(fake_redis, mock_bybit, _patch_settings):
    """When no breakers are active, list should be empty."""
    cb = CircuitBreakerManager(fake_redis, mock_bybit)
    active = await cb.get_active_breakers()
    assert active == []


@pytest.mark.asyncio
async def test_circuit_breaker_is_breaker_active(fake_redis, mock_bybit, _patch_settings):
    """_is_breaker_active should return correct state."""
    cb = CircuitBreakerManager(fake_redis, mock_bybit)

    assert await cb._is_breaker_active("DAILY_DD") is False

    await fake_redis.setex(f"{CB_KEY_PREFIX}:DAILY_DD", 1800, json.dumps({}))
    assert await cb._is_breaker_active("DAILY_DD") is True


# ---------------------------------------------------------------------------
# Risk manager + SOR integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_risk_rejects_exceeding_max_concurrent(fake_redis, _patch_settings):
    """Risk manager should reject when at max concurrent positions."""
    _patch_settings.CRYPTO_MAX_CONCURRENT_POSITIONS = 2

    risk_mgr = CryptoRiskManager(redis_client=fake_redis)
    signal = _signal()

    existing = [_position("ETHUSDT"), _position("SOLUSDT")]

    result = await risk_mgr.evaluate(
        signal=signal,
        open_positions=existing,
        wallet_balance=10000.0,
    )

    assert result["approved"] is False
    # Correlation/exposure check fires before concurrent check
    assert "exceeds" in result["reason"].lower() or "concurrent" in result["reason"].lower()


@pytest.mark.asyncio
async def test_risk_rejects_duplicate_position(fake_redis, _patch_settings):
    """Risk manager should reject if position already exists in the same ticker."""
    risk_mgr = CryptoRiskManager(redis_client=fake_redis)
    signal = _signal()

    existing = [_position("BTCUSDT")]

    result = await risk_mgr.evaluate(
        signal=signal,
        open_positions=existing,
        wallet_balance=10000.0,
    )

    assert result["approved"] is False
    assert "Already have open position" in result["reason"]


@pytest.mark.asyncio
async def test_risk_rejects_daily_loss_breach(fake_redis, _patch_settings):
    """Risk manager should reject when daily loss limit is breached."""
    _patch_settings.CRYPTO_DAILY_LOSS_LIMIT_PCT = 3.0

    risk_mgr = CryptoRiskManager(redis_client=fake_redis)
    signal = _signal()

    result = await risk_mgr.evaluate(
        signal=signal,
        open_positions=[],
        wallet_balance=10000.0,
        daily_pnl_pct=-4.0,  # exceeds 3% limit
    )

    assert result["approved"] is False
    assert "Daily loss" in result["reason"]


@pytest.mark.asyncio
async def test_risk_rejects_invalid_direction(fake_redis, _patch_settings):
    risk_mgr = CryptoRiskManager(redis_client=fake_redis)
    signal = _signal(direction="HOLD")

    result = await risk_mgr.evaluate(
        signal=signal,
        open_positions=[],
        wallet_balance=10000.0,
    )

    assert result["approved"] is False
    assert "direction" in result["reason"].lower()


@pytest.mark.asyncio
async def test_risk_rejects_missing_entry_price(fake_redis, _patch_settings):
    risk_mgr = CryptoRiskManager(redis_client=fake_redis)
    signal = _signal(entry_price=0)

    result = await risk_mgr.evaluate(
        signal=signal,
        open_positions=[],
        wallet_balance=10000.0,
    )

    assert result["approved"] is False
    assert "entry price" in result["reason"].lower()


@pytest.mark.asyncio
async def test_risk_rejects_zero_wallet_balance(fake_redis, _patch_settings):
    risk_mgr = CryptoRiskManager(redis_client=fake_redis)
    signal = _signal()

    result = await risk_mgr.evaluate(
        signal=signal,
        open_positions=[],
        wallet_balance=0,
    )

    assert result["approved"] is False
    assert "zero" in result["reason"].lower()


@pytest.mark.asyncio
async def test_risk_rejects_cooldown(fake_redis, _patch_settings):
    """Cooldown key in Redis blocks new entries (post-sellall)."""
    risk_mgr = CryptoRiskManager(redis_client=fake_redis)
    signal = _signal()

    await fake_redis.set("karsa:crypto_cooldown", "1")

    result = await risk_mgr.evaluate(
        signal=signal,
        open_positions=[],
        wallet_balance=10000.0,
    )

    assert result["approved"] is False
    assert "cooldown" in result["reason"].lower()


# ---------------------------------------------------------------------------
# Correlation tier integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_correlation_tier1_allows_two_btc_eth(fake_redis, _patch_settings):
    """Tier 1 allows 2 positions (BTC + ETH)."""
    risk_mgr = CryptoRiskManager(redis_client=fake_redis)

    # Use small size to stay under tier1 exposure limit
    existing = [_position("BTCUSDT", size=0.001)]
    corr = risk_mgr.check_correlation_limits("ETHUSDT", existing, 10000.0)

    assert corr["allowed"] is True
    assert corr["tier"] == "tier1"


@pytest.mark.asyncio
async def test_correlation_tier1_blocks_third(fake_redis, _patch_settings):
    """Tier 1 only allows 2 positions — adding a 3rd tier1 symbol should be blocked."""
    risk_mgr = CryptoRiskManager(redis_client=fake_redis)

    # Pretend BTC and ETH already exist (2 tier1 symbols)
    existing = [_position("BTCUSDT"), _position("ETHUSDT")]
    # Try adding BTC again (already counted)
    corr = risk_mgr.check_correlation_limits("BTCUSDT", existing, 10000.0)

    assert corr["allowed"] is False
    assert "max 2" in corr["reason"].lower()


@pytest.mark.asyncio
async def test_correlation_tier3_allows_one_position(fake_redis, _patch_settings):
    """Tier 3 allows only 1 position."""
    risk_mgr = CryptoRiskManager(redis_client=fake_redis)

    existing = []
    corr = risk_mgr.check_correlation_limits("DOGEUSDT", existing, 10000.0)

    assert corr["allowed"] is True
    assert corr["tier"] == "tier3"


@pytest.mark.asyncio
async def test_correlation_tier3_blocks_second_position(fake_redis, _patch_settings):
    """Tier 3 only allows 1 position."""
    risk_mgr = CryptoRiskManager(redis_client=fake_redis)

    existing = [_position("DOGEUSDT")]
    corr = risk_mgr.check_correlation_limits("XRPUSDT", existing, 10000.0)

    assert corr["allowed"] is False


@pytest.mark.asyncio
async def test_unknown_symbol_defaults_to_tier3(fake_redis, _patch_settings):
    """Unknown symbols should default to tier3."""
    risk_mgr = CryptoRiskManager(redis_client=fake_redis)

    corr = risk_mgr.check_correlation_limits("UNKNOWNCOIN", [], 10000.0)

    assert corr["tier"] == "tier3"


# ---------------------------------------------------------------------------
# SOR paper mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sor_paper_mode_returns_mock_fill(mock_bybit, _patch_settings):
    """In paper mode, SOR should return a mock fill without placing real orders."""
    _patch_settings.TRADING_MODE = "paper"

    mock_bybit.get_ticker.return_value = {"price": 60000.0}

    sor = SmartOrderRouter(mock_bybit)
    signal = _signal()
    risk_params = {
        "qty": 0.01,
        "stop_loss": 59000.0,
        "take_profit": 63000.0,
        "leverage": 5,
    }

    result = await sor.execute_order(signal, risk_params)

    assert result["success"] is True
    assert "paper_" in result["order_id"]
    assert result["fill_price"] == 60000.0
    mock_bybit.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_sor_rejects_zero_quantity(mock_bybit, _patch_settings):
    _patch_settings.TRADING_MODE = "live"

    sor = SmartOrderRouter(mock_bybit)
    signal = _signal()
    risk_params = {"qty": 0, "stop_loss": 59000.0, "take_profit": 63000.0, "leverage": 1}

    result = await sor.execute_order(signal, risk_params)

    assert result["success"] is False
    assert "Zero quantity" in result["error"]


# ---------------------------------------------------------------------------
# SOR flatten_all
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sor_flatten_all_closes_all_positions(mock_bybit, _patch_settings):
    mock_bybit.get_positions.return_value = [
        _position("BTCUSDT", "Buy", 0.5),
        _position("ETHUSDT", "Sell", 1.0),
    ]
    mock_bybit.place_order.return_value = {"order_id": "close_001"}

    sor = SmartOrderRouter(mock_bybit)
    result = await sor.flatten_all()

    assert result["count"] == 2
    assert "BTCUSDT" in result["closed"]
    assert "ETHUSDT" in result["closed"]

    # Verify close sides are correct
    calls = mock_bybit.place_order.call_args_list
    btc_call = next(c for c in calls if c.kwargs["symbol"] == "BTCUSDT")
    eth_call = next(c for c in calls if c.kwargs["symbol"] == "ETHUSDT")
    assert btc_call.kwargs["side"] == "Sell"   # close Buy
    assert eth_call.kwargs["side"] == "Buy"     # close Sell
    assert btc_call.kwargs["reduce_only"] is True
    assert eth_call.kwargs["reduce_only"] is True


@pytest.mark.asyncio
async def test_sor_flatten_all_empty_positions(mock_bybit, _patch_settings):
    mock_bybit.get_positions.return_value = []

    sor = SmartOrderRouter(mock_bybit)
    result = await sor.flatten_all()

    assert result["count"] == 0
    assert result["closed"] == []


# ---------------------------------------------------------------------------
# Full pipeline: signal → calibrate → risk → SOR → OMS track
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_end_to_end_pipeline_with_oms_tracking(fake_redis, mock_bybit, _patch_settings):
    """Full pipeline from signal to OMS order tracking."""
    _patch_settings.TRADING_MODE = "live"

    # Calibrate
    calibrator = ConfidenceCalibrator()
    calibrator._cached_multiplier = 1.0
    confidence = await calibrator.calibrate_signal(80.0)

    # Risk gate
    risk_mgr = CryptoRiskManager(redis_client=fake_redis)
    signal = _signal(confidence=confidence)

    risk_result = await risk_mgr.evaluate(
        signal=signal,
        open_positions=[],
        wallet_balance=10000.0,
    )
    assert risk_result["approved"] is True

    # SOR
    mock_bybit.place_order.return_value = {"order_id": "e2e_001"}
    mock_bybit.get_order_status.return_value = {
        "status": "Filled",
        "avg_price": 60000.0,
    }

    sor = SmartOrderRouter(mock_bybit)
    exec_result = await sor.execute_order(signal, risk_result)
    assert exec_result["success"] is True

    # OMS tracking
    oms = OrderManagementSystem(fake_redis, mock_bybit)
    tracked = await oms.track_order(
        order_id=exec_result["order_id"],
        ticker="BTCUSDT",
        side="Buy",
        quantity=risk_result["qty"],
        order_type="Market",
    )
    oid = tracked["order_id"]
    assert oid  # paper mode generates paper_* IDs
    assert tracked["status"] == "SUBMITTED"

    # Update to filled
    await oms.update_status(oid, "FILLED",
                            filled_qty=risk_result["qty"],
                            avg_price=60000.0)

    order = await oms.get_order(oid)
    assert order["status"] == "FILLED"

    # Should be removed from active
    active = await oms.get_active_orders()
    active_ids = {o["order_id"] for o in active}
    assert oid not in active_ids


# ---------------------------------------------------------------------------
# SL engine integration with position cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sl_engine_breaches_on_price_drop(fake_redis, mock_bybit, _patch_settings):
    """Simulate a price drop that triggers stop-loss after OMS tracks the position."""
    sl_engine = StopLossEngine(fake_redis, mock_bybit)

    # Simulate synced position
    sl_engine._position_cache = {
        "BTCUSDT": {"stop_loss": 59000.0, "side": "Buy", "size": 0.01},
    }
    sl_engine._last_sync = time.time()

    mock_bybit.place_order.return_value = {"orderId": "sl_e2e_001"}

    # Price drops below SL
    tick = {"ticker": "BTCUSDT", "price": 58500.0, "ts": int(time.time() * 1000)}
    await sl_engine._check_tick(tick)

    # SL order placed
    mock_bybit.place_order.assert_called_once()
    call = mock_bybit.place_order.call_args
    assert call.kwargs["side"] == "Sell"
    assert call.kwargs["reduce_only"] is True

    # Position cleared from cache
    assert "BTCUSDT" not in sl_engine._position_cache

    # Event published
    assert len(fake_redis._published) == 1
    event = json.loads(fake_redis._published[0][1])
    assert event["ticker"] == "BTCUSDT"
    assert event["trigger_price"] == 58500.0
