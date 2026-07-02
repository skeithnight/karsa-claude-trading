"""Shared pytest fixtures for Karsa trading system tests."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Async event loop (session-scoped)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    """Override default event loop to session scope for faster tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Redis mock
# ---------------------------------------------------------------------------

class FakeRedis:
    """Minimal async Redis mock backed by an in-memory dict.

    Supports the subset of the redis.asyncio API used across Karsa modules:
    get/set/setex/delete/sadd/srem/smembers/scan_iter/publish/ttl.
    """

    def __init__(self):
        self._store: dict[str, str] = {}
        self._sets: dict[str, set] = {}
        self._published: list[tuple[str, str]] = []

    async def get(self, key: str):
        return self._store.get(key)

    async def set(self, key: str, value, nx: bool = False, **kwargs):
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: str):
        self._store[key] = value

    async def delete(self, *keys):
        for k in keys:
            self._store.pop(k, None)
            self._sets.pop(k, None)

    async def sadd(self, key: str, *values):
        self._sets.setdefault(key, set()).update(values)

    async def srem(self, key: str, *values):
        if key in self._sets:
            self._sets[key] -= set(values)

    async def smembers(self, key: str):
        return list(self._sets.get(key, set()))

    async def scan_iter(self, match: str = "*", count: int = 10):
        """Yield keys matching a simple glob pattern."""
        import fnmatch
        for key in list(self._store.keys()) + list(self._sets.keys()):
            if fnmatch.fnmatch(key, match):
                yield key

    async def publish(self, channel: str, message: str):
        self._published.append((channel, message))

    async def ttl(self, key: str):
        if key in self._store:
            return 1800  # arbitrary non-expired value
        return -2

    async def close(self):
        pass


@pytest.fixture
def fake_redis():
    """Fresh FakeRedis instance per test."""
    return FakeRedis()


@pytest.fixture
def mock_redis():
    """AsyncMock redis — use when tests need explicit side_effect control."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.setex = AsyncMock()
    redis.delete = AsyncMock()
    redis.sadd = AsyncMock()
    redis.srem = AsyncMock()
    redis.smembers = AsyncMock(return_value=[])
    redis.publish = AsyncMock()
    redis.ttl = AsyncMock(return_value=1800)

    async def _scan(match="*", count=10):
        return
        yield  # make it an async generator

    redis.scan_iter = _scan
    return redis


# ---------------------------------------------------------------------------
# Bybit mock
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_bybit():
    """BybitClient mock with common methods."""
    bybit = AsyncMock()
    bybit.get_positions = AsyncMock(return_value=[])
    bybit.get_wallet_balance = AsyncMock(return_value={"balance": 10000.0})
    bybit.place_order = AsyncMock(return_value={"order_id": "ord_001"})
    bybit.cancel_order = AsyncMock(return_value=True)
    bybit.get_orderbook = AsyncMock(return_value={
        "bids": [[60000.0, 1.5]],
        "asks": [[60001.0, 1.5]],
    })
    bybit.get_ticker = AsyncMock(return_value={"price": 60000.0})
    bybit.get_open_orders = AsyncMock(return_value=[])
    bybit.get_order_status = AsyncMock(return_value={
        "status": "Filled",
        "avg_price": 60000.0,
    })
    bybit.set_stop_loss = AsyncMock(return_value={"order_id": "sl_001"})
    bybit.set_take_profit = AsyncMock(return_value={"order_id": "tp_001"})
    bybit.get_funding_rate = AsyncMock(return_value={"funding_rate": 0.0001})

    # _http_client for pybit direct calls
    bybit._http_client = MagicMock()
    bybit._http_client.set_leverage = MagicMock()
    bybit._http_client.get_kline = MagicMock(return_value={"result": {"list": []}})

    return bybit


# ---------------------------------------------------------------------------
# DB session mock
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_session():
    """Mock async SQLAlchemy session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.close = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Settings patch (avoid .env dependency)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_settings():
    """Patch settings to safe test defaults."""
    with patch("src.config.settings") as mock_settings:
        mock_settings.REDIS_URL = "redis://localhost:6379"
        mock_settings.REDIS_PREFIX = "karsa"
        mock_settings.TRADING_MODE = "paper"
        mock_settings.CRYPTO_MAX_RISK_PER_TRADE_PCT = 1.0
        mock_settings.CRYPTO_MAX_POSITION_PCT = 10.0
        mock_settings.CRYPTO_MAX_CONCURRENT_POSITIONS = 5
        mock_settings.CRYPTO_DAILY_LOSS_LIMIT_PCT = 3.0
        mock_settings.CRYPTO_MAX_LEVERAGE = 10
        mock_settings.CRYPTO_LIQUIDATION_WARN_PCT = 20.0
        mock_settings.CRYPTO_LIQUIDATION_ALERT_PCT = 10.0
        mock_settings.CRYPTO_LIQUIDATION_FORCE_CLOSE_PCT = 5.0
        mock_settings.CRYPTO_FUNDING_ALERT_THRESHOLD = 0.05
        mock_settings.BYBIT_TESTNET = True
        mock_settings.BYBIT_API_KEY = "test_key"
        mock_settings.BYBIT_API_SECRET = "test_secret"
        yield mock_settings
