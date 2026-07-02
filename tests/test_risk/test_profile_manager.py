"""Tests for RiskProfileManager — profiles, switching, position params."""

import pytest
from unittest.mock import AsyncMock, patch
from decimal import Decimal

from src.risk.profile_manager import (
    RiskProfileManager, RiskProfile,
    PROFILES, HARD_MAX_POSITION_SIZE_PCT, HARD_MAX_DAILY_LOSS_PCT,
    PROFILE_CHANGE_COOLDOWN_SECONDS,
)


@pytest.fixture
def redis_client():
    r = AsyncMock()
    r.get.return_value = None
    r.set.return_value = True
    r.setex.return_value = True
    return r


@pytest.fixture
def manager(redis_client):
    return RiskProfileManager(redis_client=redis_client)


class TestProfileConfigs:
    def test_all_profiles_exist(self):
        assert RiskProfile.CONSERVATIVE in PROFILES
        assert RiskProfile.SEMI_AGGRESSIVE in PROFILES
        assert RiskProfile.AGGRESSIVE in PROFILES

    def test_conservative_most_restrictive(self):
        c = PROFILES[RiskProfile.CONSERVATIVE]
        s = PROFILES[RiskProfile.SEMI_AGGRESSIVE]
        a = PROFILES[RiskProfile.AGGRESSIVE]
        assert c.max_position_size_pct <= s.max_position_size_pct <= a.max_position_size_pct

    def test_hard_limits_respected(self):
        for profile in PROFILES.values():
            assert profile.max_position_size_pct <= HARD_MAX_POSITION_SIZE_PCT

    def test_all_profiles_have_required_fields(self):
        for p in PROFILES.values():
            assert p.name
            assert p.size_multiplier > 0
            assert p.stop_loss_atr_mult > 0
            assert p.take_profit_atr_mult > 0
            assert p.max_open_positions >= 1


class TestGetActiveProfile:
    @pytest.mark.asyncio
    async def test_default_conservative(self, manager, redis_client):
        redis_client.get.return_value = None
        profile = await manager.get_active_profile()
        assert profile.name == RiskProfile.CONSERVATIVE.value

    @pytest.mark.asyncio
    async def test_reads_from_redis(self, manager, redis_client):
        redis_client.get.return_value = RiskProfile.AGGRESSIVE.value
        profile = await manager.get_active_profile()
        assert profile.name == RiskProfile.AGGRESSIVE.value

    @pytest.mark.asyncio
    async def test_redis_error_defaults(self, manager, redis_client):
        redis_client.get.side_effect = Exception("Redis down")
        profile = await manager.get_active_profile()
        assert profile.name == RiskProfile.CONSERVATIVE.value


class TestSwitchProfile:
    @pytest.mark.asyncio
    async def test_switch_success(self, manager, redis_client):
        redis_client.get.return_value = RiskProfile.CONSERVATIVE.value
        redis_client.exists = AsyncMock(return_value=False)
        result = await manager.set_profile(RiskProfile.AGGRESSIVE, "user request")
        assert result is True

    @pytest.mark.asyncio
    async def test_same_profile_noop(self, manager, redis_client):
        redis_client.get.return_value = RiskProfile.CONSERVATIVE.value
        redis_client.exists = AsyncMock(return_value=False)
        result = await manager.set_profile(RiskProfile.CONSERVATIVE, "test")
        assert result is True

    @pytest.mark.asyncio
    async def test_cooldown_active(self, manager, redis_client):
        redis_client.get.return_value = RiskProfile.CONSERVATIVE.value
        redis_client.exists = AsyncMock(return_value=True)
        result = await manager.set_profile(RiskProfile.AGGRESSIVE, "test")
        assert result is False


class TestCalculatePositionSize:
    @pytest.mark.asyncio
    async def test_long_direction(self, manager, redis_client):
        redis_client.get.return_value = RiskProfile.CONSERVATIVE.value
        result = await manager.calculate_position_size(
            equity=10000.0, atr=2.0, entry_price=100.0, direction="LONG"
        )
        assert result["stop_loss"] < 100.0
        assert result["take_profit"] > 100.0
        assert result["quantity"] > 0
        assert result["rr_ratio"] > 0

    @pytest.mark.asyncio
    async def test_short_direction(self, manager, redis_client):
        redis_client.get.return_value = RiskProfile.CONSERVATIVE.value
        result = await manager.calculate_position_size(
            equity=10000.0, atr=2.0, entry_price=100.0, direction="SHORT"
        )
        assert result["stop_loss"] > 100.0
        assert result["take_profit"] < 100.0

    @pytest.mark.asyncio
    async def test_zero_atr(self, manager, redis_client):
        redis_client.get.return_value = RiskProfile.CONSERVATIVE.value
        result = await manager.calculate_position_size(
            equity=10000.0, atr=0.0, entry_price=100.0, direction="LONG"
        )
        assert result["quantity"] == 0
        assert result["rr_ratio"] == 0

    @pytest.mark.asyncio
    async def test_profiles_use_different_multipliers(self, manager, redis_client):
        redis_client.get.return_value = RiskProfile.CONSERVATIVE.value
        r1 = await manager.calculate_position_size(10000.0, 2.0, 100.0, "LONG")
        redis_client.get.return_value = RiskProfile.AGGRESSIVE.value
        r2 = await manager.calculate_position_size(10000.0, 2.0, 100.0, "LONG")
        assert r2["take_profit"] >= r1["take_profit"]
