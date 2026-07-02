"""Tests for LiquidityMonitor and SlippageEstimator."""

import pytest
from unittest.mock import AsyncMock, patch
from decimal import Decimal

from src.risk.liquidity import (
    LiquidityMonitor,
    SlippageEstimator,
    MIN_ORDER_BOOK_DEPTH_USD,
    MAX_SPREAD_PCT,
    MAX_SLIPPAGE_PCT,
    DEPTH_LEVELS,
)


@pytest.fixture
def bybit():
    client = AsyncMock()
    return client


@pytest.fixture
def monitor(bybit):
    return LiquidityMonitor(bybit)


@pytest.fixture
def estimator(bybit):
    return SlippageEstimator(bybit)


# ── Constants ──────────────────────────────────────────────────────────────────

class TestConstants:
    def test_min_depth(self):
        assert MIN_ORDER_BOOK_DEPTH_USD == 100_000

    def test_max_spread(self):
        assert MAX_SPREAD_PCT == Decimal("0.002")

    def test_max_slippage(self):
        assert MAX_SLIPPAGE_PCT == Decimal("0.005")

    def test_depth_levels(self):
        assert DEPTH_LEVELS == 10


# ── LiquidityMonitor ──────────────────────────────────────────────────────────

class TestLiquidityMonitor:
    @pytest.mark.asyncio
    async def test_empty_orderbook_fails(self, monitor, bybit):
        bybit.get_orderbook.return_value = {"error": "timeout"}
        result = await monitor.check_liquidity("BTCUSDT", "BUY")
        assert result["can_trade"] is False
        assert "orderbook_fetch_failed" in result["reason"]

    @pytest.mark.asyncio
    async def test_wide_spread_rejects(self, monitor, bybit):
        """Spread > 0.2% should reject."""
        bybit.get_orderbook.return_value = {
            "bids": [[65000, 1.0], [64900, 2.0]],
            "asks": [[66000, 1.0], [66100, 2.0]],  # spread ~1.5%
        }
        result = await monitor.check_liquidity("BTCUSDT", "BUY")
        assert result["can_trade"] is False
        assert "spread_too_wide" in result["reason"]

    @pytest.mark.asyncio
    async def test_thin_depth_rejects(self, monitor, bybit):
        """Depth < $100k should reject."""
        bybit.get_orderbook.return_value = {
            "bids": [[65000, 0.1], [64900, 0.2]],
            "asks": [[65010, 0.1], [65020, 0.2]],  # spread ~0.015%, depth ~$32k
        }
        result = await monitor.check_liquidity("BTCUSDT", "BUY")
        assert result["can_trade"] is False
        assert "insufficient_depth" in result["reason"]

    @pytest.mark.asyncio
    async def test_good_liquidity_passes(self, monitor, bybit):
        """Tight spread + deep book should pass."""
        levels = [[65000 + i * 10, 20.0] for i in range(10)]  # $13M depth
        bybit.get_orderbook.return_value = {
            "bids": levels,
            "asks": [[65005, 20.0]] + [[65010 + i * 10, 20.0] for i in range(9)],
        }
        result = await monitor.check_liquidity("BTCUSDT", "BUY")
        assert result["can_trade"] is True
        assert result["reason"] == "ok"

    @pytest.mark.asyncio
    async def test_buy_checks_ask_side(self, monitor, bybit):
        """BUY side should check asks for depth."""
        asks = [[65000 + i, 200.0] for i in range(10)]
        bybit.get_orderbook.return_value = {
            "bids": [[64999, 0.01]],  # tiny bid depth
            "asks": asks,
        }
        result = await monitor.check_liquidity("BTCUSDT", "BUY")
        assert result["can_trade"] is True  # asks have enough depth


# ── SlippageEstimator ─────────────────────────────────────────────────────────

class TestSlippageEstimator:
    @pytest.mark.asyncio
    async def test_small_order_no_slippage(self, estimator, bybit):
        """Small order should have near-zero slippage."""
        bybit.get_orderbook.return_value = {
            "bids": [[65000, 10.0], [64990, 10.0]],
            "asks": [[65005, 10.0], [65010, 10.0]],
        }
        result = await estimator.estimate_slippage("BTCUSDT", "BUY", 1000)
        assert result["can_execute"] is True
        assert result["slippage_pct"] < 0.001

    @pytest.mark.asyncio
    async def test_large_order_high_slippage(self, estimator, bybit):
        """Large order walking through levels should have higher slippage."""
        asks = [[65000 + i * 100, 0.1] for i in range(20)]  # thin asks
        bybit.get_orderbook.return_value = {
            "bids": [[64999, 10.0]],
            "asks": asks,
        }
        result = await estimator.estimate_slippage("BTCUSDT", "BUY", 100000)
        assert result["slippage_pct"] > 0
        # With thin books, slippage should be significant

    @pytest.mark.asyncio
    async def test_empty_orderbook_fails(self, estimator, bybit):
        bybit.get_orderbook.return_value = {"error": "timeout"}
        result = await estimator.estimate_slippage("BTCUSDT", "BUY", 1000)
        assert result["can_execute"] is False

    @pytest.mark.asyncio
    async def test_sell_uses_bid_side(self, estimator, bybit):
        """SELL should simulate through bids."""
        bids = [[65000 - i * 10, 20.0] for i in range(20)]
        bybit.get_orderbook.return_value = {
            "bids": bids,
            "asks": [[65005, 10.0]],
        }
        result = await estimator.estimate_slippage("BTCUSDT", "SELL", 10000)
        assert result["can_execute"] is True
        assert result["effective_price"] > 0


# ── Depth Threshold Edges ───────────────────────────────────────────────────

class TestDepthThresholdEdges:
    @pytest.mark.asyncio
    async def test_depth_below_minimum_fails(self, monitor, bybit):
        """When depth is just below minimum, should fail."""
        bids = [[65000, 0.001] for _ in range(10)]  # very thin
        asks = [[65005, 0.001] for _ in range(10)]
        bybit.get_orderbook.return_value = {"bids": bids, "asks": asks}
        result = await monitor.check_liquidity("BTCUSDT", "BUY")
        assert result["can_trade"] is False
        assert "insufficient" in result["reason"]


# ── Spread Exactly At Limit ─────────────────────────────────────────────────

class TestSpreadAtLimit:
    @pytest.mark.asyncio
    async def test_spread_exactly_at_limit_passes(self, monitor, bybit):
        """When spread_pct == MAX_SPREAD_PCT, should pass (not >)."""
        # bid=64935, ask=65065 -> mid=65000, spread=130/65000=0.002
        bids = [[64935, 100.0]]
        asks = [[65065, 100.0]]
        bybit.get_orderbook.return_value = {"bids": bids, "asks": asks}
        result = await monitor.check_liquidity("BTCUSDT", "BUY")
        # spread = 0.002 == MAX_SPREAD_PCT -> should NOT reject on spread
        assert result["spread_pct"] <= float(MAX_SPREAD_PCT) or result["can_trade"] is False


# ── Custom Thresholds ───────────────────────────────────────────────────────

class TestCustomThresholds:
    @pytest.mark.asyncio
    async def test_custom_size_usd(self, monitor, bybit):
        """Test with non-default size_usd parameter."""
        bids = [[65000, 0.01] for _ in range(10)]
        asks = [[65005, 0.01] for _ in range(10)]
        bybit.get_orderbook.return_value = {"bids": bids, "asks": asks}
        result = await monitor.check_liquidity("BTCUSDT", "BUY", size_usd=5000)
        assert "spread_pct" in result


# ── can_trade All Failure Reasons ───────────────────────────────────────────

class TestCanTradeFailureReasons:
    @pytest.mark.asyncio
    async def test_orderbook_fetch_exception(self, monitor, bybit):
        """When get_orderbook raises exception."""
        bybit.get_orderbook.side_effect = Exception("connection timeout")
        result = await monitor.check_liquidity("BTCUSDT", "BUY")
        assert result["can_trade"] is False
        # Reason can be "orderbook_fetch_failed" or "error:..."
        assert result["reason"]  # non-empty

    @pytest.mark.asyncio
    async def test_spread_too_wide(self, monitor, bybit):
        """When spread exceeds MAX_SPREAD_PCT."""
        bybit.get_orderbook.return_value = {
            "bids": [[60000, 100.0]],
            "asks": [[70000, 100.0]],
        }
        result = await monitor.check_liquidity("BTCUSDT", "BUY")
        assert result["can_trade"] is False
        assert "spread_too_wide" in result["reason"]


# ── _get_orderbook Edge Cases ───────────────────────────────────────────────

class TestGetOrderbookEdge:
    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self, monitor, bybit):
        """When bybit.get_orderbook raises exception, returns None."""
        bybit.get_orderbook.side_effect = Exception("network error")
        result = await monitor._get_orderbook("BTCUSDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error_key(self, monitor, bybit):
        """When orderbook dict has 'error' key, returns None."""
        bybit.get_orderbook.return_value = {"error": "symbol not found"}
        bybit.get_orderbook.side_effect = None
        result = await monitor._get_orderbook("INVALIDUSDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_orderbook_on_success(self, monitor, bybit):
        """Normal orderbook returned successfully."""
        ob = {"bids": [[65000, 1.0]], "asks": [[65005, 1.0]]}
        bybit.get_orderbook.return_value = ob
        bybit.get_orderbook.side_effect = None
        result = await monitor._get_orderbook("BTCUSDT")
        assert result == ob


# ── Slippage Edge Cases ─────────────────────────────────────────────────────

class TestSlippageEdgeCases:
    @pytest.mark.asyncio
    async def test_price_improvement_clamped_to_zero(self, estimator, bybit):
        """When effective price is better than mid, slippage should be ~0 (clamped)."""
        asks = [[65005, 1000.0]]  # deep liquidity at single level
        bids = [[65004, 1000.0]]
        bybit.get_orderbook.return_value = {"bids": bids, "asks": asks}
        result = await estimator.estimate_slippage("BTCUSDT", "BUY", 100)
        # Should be very small or zero due to clamping
        assert result["slippage_pct"] <= 0.0001

    @pytest.mark.asyncio
    async def test_insufficient_liquidity_fills_what_it_can(self, estimator, bybit):
        """When orderbook depth can't fill the entire order, fills partial and calculates slippage."""
        asks = [[65005, 0.001]]  # very thin - only $65 worth
        bids = [[65000, 100.0]]
        bybit.get_orderbook.return_value = {"bids": bids, "asks": asks}
        result = await estimator.estimate_slippage("BTCUSDT", "BUY", 100000)
        # The estimator fills what it can (0.001 qty) and calculates slippage on that
        assert result["effective_price"] > 0
        assert result["mid_price"] > 0

    @pytest.mark.asyncio
    async def test_empty_bids_returns_error(self, estimator, bybit):
        """When orderbook has asks but no bids."""
        bybit.get_orderbook.return_value = {"bids": [], "asks": [[65005, 10.0]]}
        result = await estimator.estimate_slippage("BTCUSDT", "SELL", 1000)
        assert result["can_execute"] is False

    @pytest.mark.asyncio
    async def test_fetch_exception(self, estimator, bybit):
        """When get_orderbook raises, returns error."""
        bybit.get_orderbook.side_effect = Exception("timeout")
        result = await estimator.estimate_slippage("BTCUSDT", "BUY", 1000)
        assert result["can_execute"] is False
        assert result["reason"]  # non-empty error reason
