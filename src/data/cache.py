"""Karsa Trading System - Redis Cache Operations"""

import json
from decimal import Decimal
from datetime import datetime

import redis.asyncio as redis

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("cache")


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class CacheManager:
    """Redis cache manager for market data and state."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def _key(self, *parts: str) -> str:
        return ":".join([settings.REDIS_PREFIX] + list(parts))

    async def get_quote(self, ticker: str, market: str) -> dict | None:
        key = self._key("quote", market, ticker)
        data = await self.redis.get(key)
        return json.loads(data) if data else None

    async def set_quote(self, ticker: str, market: str, quote: dict) -> None:
        key = self._key("quote", market, ticker)
        await self.redis.setex(key, 60, json.dumps(quote, cls=DecimalEncoder))

    async def get_ohlcv(self, ticker: str, market: str, timeframe: str) -> list[dict] | None:
        key = self._key("ohlcv", market, ticker, timeframe)
        data = await self.redis.get(key)
        return json.loads(data) if data else None

    async def set_ohlcv(self, ticker: str, market: str, timeframe: str, candles: list[dict]) -> None:
        key = self._key("ohlcv", market, ticker, timeframe)
        await self.redis.setex(key, 3600, json.dumps(candles, cls=DecimalEncoder))

    async def get_foreign_flow(self, ticker: str) -> dict | None:
        key = self._key("foreign_flow", ticker)
        data = await self.redis.get(key)
        return json.loads(data) if data else None

    async def set_foreign_flow(self, ticker: str, flow_data: dict, ttl: int = 86400) -> None:
        key = self._key("foreign_flow", ticker)
        await self.redis.setex(key, ttl, json.dumps(flow_data, cls=DecimalEncoder))

    async def get_portfolio(self, market: str) -> list[dict] | None:
        key = self._key("portfolio", market)
        data = await self.redis.get(key)
        return json.loads(data) if data else None

    async def set_portfolio(self, market: str, positions: list[dict]) -> None:
        key = self._key("portfolio", market)
        await self.redis.setex(key, 300, json.dumps(positions, cls=DecimalEncoder))

    async def invalidate_portfolio(self, market: str) -> None:
        key = self._key("portfolio", market)
        await self.redis.delete(key)

    async def publish_signal(self, signal_data: dict) -> None:
        channel = self._key("signals")
        await self.redis.publish(channel, json.dumps(signal_data, cls=DecimalEncoder))

    async def subscribe_signals(self):
        pubsub = self.redis.pubsub()
        channel = self._key("signals")
        await pubsub.subscribe(channel)
        return pubsub

    async def publish_approval(self, approval_data: dict) -> None:
        channel = self._key("approvals")
        await self.redis.publish(channel, json.dumps(approval_data, cls=DecimalEncoder))

    async def subscribe_approvals(self):
        pubsub = self.redis.pubsub()
        channel = self._key("approvals")
        await pubsub.subscribe(channel)
        return pubsub

    async def ping(self) -> bool:
        try:
            return await self.redis.ping()
        except Exception:
            return False
