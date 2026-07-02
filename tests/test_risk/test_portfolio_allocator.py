"""Tests for PortfolioAllocator — market limits and global drawdown."""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock

from src.risk.portfolio_allocator import (
    PortfolioAllocator, MARKET_LIMITS, GLOBAL_DRAWDOWN_LIMIT,
)


@pytest.fixture
def redis_client():
    r = AsyncMock()
    r.get.return_value = None
    return r


@pytest.fixture
def allocator(redis_client):
    return PortfolioAllocator(redis_client=redis_client)


class TestMarketLimits:
    def test_crypto_30_pct(self):
        assert MARKET_LIMITS["CRYPTO"] == Decimal("0.30")

    def test_us_40_pct(self):
        assert MARKET_LIMITS["US"] == Decimal("0.40")

    def test_etf_20_pct(self):
        assert MARKET_LIMITS["ETF"] == Decimal("0.20")

    def test_idx_10_pct(self):
        assert MARKET_LIMITS["IDX"] == Decimal("0.10")

    def test_sum_to_100_pct(self):
        assert sum(MARKET_LIMITS.values()) == Decimal("1.00")


class TestCheckGlobalDrawdown:
    @pytest.mark.asyncio
    async def test_no_initial_equity(self, allocator):
        allocator._initial_equity = None
        ok, reason = await allocator._check_global_drawdown(Decimal("10000"))
        assert ok is True
        assert reason == "no_initial_equity"

    @pytest.mark.asyncio
    async def test_zero_initial_equity(self, allocator):
        allocator._initial_equity = Decimal("0")
        ok, reason = await allocator._check_global_drawdown(Decimal("10000"))
        assert ok is True

    @pytest.mark.asyncio
    async def test_within_limit(self, allocator):
        allocator._initial_equity = Decimal("100000")
        ok, reason = await allocator._check_global_drawdown(Decimal("97000"))
        assert ok is True
        assert reason == "ok"

    @pytest.mark.asyncio
    async def test_exceeds_limit(self, allocator):
        allocator._initial_equity = Decimal("100000")
        ok, reason = await allocator._check_global_drawdown(Decimal("94000"))
        assert ok is False
        assert "drawdown" in reason.lower()

    @pytest.mark.asyncio
    async def test_exactly_at_limit(self, allocator):
        allocator._initial_equity = Decimal("100000")
        ok, reason = await allocator._check_global_drawdown(Decimal("95000"))
        assert ok is False

    @pytest.mark.asyncio
    async def test_negative_equity(self, allocator):
        allocator._initial_equity = Decimal("100000")
        ok, reason = await allocator._check_global_drawdown(Decimal("-1000"))
        assert ok is False


class TestCanTrade:
    @pytest.mark.asyncio
    async def test_within_limit_allows(self, allocator):
        with patch.object(allocator, "_get_market_exposure", return_value=Decimal("0")), \
             patch.object(allocator, "_get_total_equity", return_value=Decimal("100000")), \
             patch.object(allocator, "_check_global_drawdown", return_value=(True, "ok")):
            ok, reason = await allocator.can_trade("US", Decimal("10000"))
        assert ok is True

    @pytest.mark.asyncio
    async def test_exceeds_market_limit(self, allocator):
        with patch.object(allocator, "_get_market_exposure", return_value=Decimal("35000")), \
             patch.object(allocator, "_get_total_equity", return_value=Decimal("100000")), \
             patch.object(allocator, "_check_global_drawdown", return_value=(True, "ok")):
            ok, reason = await allocator.can_trade("IDX", Decimal("10000"))
        # IDX limit is 10% = $10,000. Exposure $35k already exceeds.
        assert ok is False
