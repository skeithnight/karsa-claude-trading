"""Redis pub/sub event bus — cross-process event delivery.

ponytail: wraps InProcessEventBus + publishes to Redis channel.
Other processes subscribe to the same channel for event delivery.
"""
from __future__ import annotations
import asyncio
import json
from typing import Callable, Awaitable
import structlog

from .base import EventEnvelope
from .in_process import InProcessEventBus

logger = structlog.get_logger(__name__)

CHANNEL = "karsa:events:domain"


class RedisEventBus(InProcessEventBus):
    """In-process + Redis pub/sub hybrid event bus.

    Events are dispatched in-process AND published to Redis channel.
    Other processes can subscribe to the Redis channel for cross-process delivery.
    """

    def __init__(self, redis_client=None):
        super().__init__()
        self._redis = redis_client

    def set_redis(self, redis_client):
        self._redis = redis_client

    async def publish(self, event: EventEnvelope) -> None:
        # In-process dispatch
        await super().publish(event)

        # Redis pub/sub (cross-process)
        if self._redis:
            try:
                data = {
                    "event_type": event.event_type,
                    "aggregate_id": event.aggregate_id,
                    "aggregate_type": event.aggregate_type,
                    "publisher": event.publisher,
                    "payload": event.payload,
                }
                await self._redis.publish(CHANNEL, json.dumps(data, default=str))
                logger.debug("redis_event_published", event_type=event.event_type)
            except Exception as e:
                logger.warning("redis_publish_failed", error=str(e))


async def subscribe_redis_events(redis_client, callback: Callable[[dict], Awaitable[None]]):
    """Subscribe to Redis channel for cross-process events. Returns pubsub object."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(CHANNEL)
    logger.info("redis_event_subscribed", channel=CHANNEL)

    async def listen():
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    await callback(data)
                except Exception as e:
                    logger.warning("redis_event_callback_failed", error=str(e))

    asyncio.create_task(listen())
    return pubsub
