"""Tests for emergency stop / kill switch — Redis-backed."""

import json
import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture
def redis_client():
    r = AsyncMock()
    r.get.return_value = None
    r.set.return_value = True
    r.delete.return_value = 1
    return r


@pytest.fixture(autouse=True)
def patch_redis(redis_client):
    with patch("src.risk.emergency._get_redis", return_value=redis_client), \
         patch("src.risk.emergency.update_kill_switch"):
        yield


class TestActivate:
    @pytest.mark.asyncio
    async def test_sets_key_with_nx(self, redis_client):
        from src.risk import emergency
        redis_client.set.return_value = True
        result = await emergency.activate("test reason", "operator1")
        assert result is True
        redis_client.set.assert_called_once()
        call_args = redis_client.set.call_args
        assert call_args.kwargs.get("nx") is True

    @pytest.mark.asyncio
    async def test_returns_false_if_already_active(self, redis_client):
        from src.risk import emergency
        redis_client.set.return_value = None
        result = await emergency.activate("test reason", "operator1")
        assert result is False


class TestDeactivate:
    @pytest.mark.asyncio
    async def test_deletes_key(self, redis_client):
        from src.risk import emergency
        await emergency.deactivate("operator1")
        redis_client.delete.assert_called_once()


class TestIsActive:
    @pytest.mark.asyncio
    async def test_active_true(self, redis_client):
        from src.risk import emergency
        redis_client.get.return_value = json.dumps({"active": True, "reason": "test"})
        result = await emergency.is_active()
        assert result is True

    @pytest.mark.asyncio
    async def test_active_false_in_json(self, redis_client):
        from src.risk import emergency
        redis_client.get.return_value = json.dumps({"active": False})
        result = await emergency.is_active()
        assert result is False

    @pytest.mark.asyncio
    async def test_key_missing(self, redis_client):
        from src.risk import emergency
        redis_client.get.return_value = None
        result = await emergency.is_active()
        assert result is False


class TestGetStatus:
    @pytest.mark.asyncio
    async def test_returns_full_payload(self, redis_client):
        from src.risk import emergency
        payload = {"active": True, "reason": "crash", "operator": "bot"}
        redis_client.get.return_value = json.dumps(payload)
        result = await emergency.get_status()
        assert result == payload

    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self, redis_client):
        from src.risk import emergency
        redis_client.get.return_value = None
        result = await emergency.get_status()
        assert result is None


class TestGlobalHalt:
    @pytest.mark.asyncio
    async def test_activate_sets_both_keys(self, redis_client):
        from src.risk import emergency
        redis_client.set.return_value = True
        result = await emergency.activate_global_halt("emergency", "admin")
        assert result is True
        assert redis_client.set.call_count == 2

    @pytest.mark.asyncio
    async def test_activate_returns_false_if_exists(self, redis_client):
        from src.risk import emergency
        redis_client.set.return_value = None
        result = await emergency.activate_global_halt("test", "admin")
        assert result is False

    @pytest.mark.asyncio
    async def test_deactivate_deletes_both(self, redis_client):
        from src.risk import emergency
        await emergency.deactivate_global_halt("admin")
        assert redis_client.delete.call_count == 2

    @pytest.mark.asyncio
    async def test_is_global_halt_true(self, redis_client):
        from src.risk import emergency
        redis_client.get.return_value = json.dumps({"active": True})
        result = await emergency.is_global_halt()
        assert result is True

    @pytest.mark.asyncio
    async def test_is_global_halt_false(self, redis_client):
        from src.risk import emergency
        redis_client.get.return_value = None
        result = await emergency.is_global_halt()
        assert result is False
