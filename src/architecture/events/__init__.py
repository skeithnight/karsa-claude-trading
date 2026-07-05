"""Event-driven communication layer.

Module-level singleton: import `event_bus` or `publish_event` from here.
publish_event() checks the `event_bus_enabled` feature flag — returns immediately if disabled.
"""
from __future__ import annotations
from typing import Any, Dict, Optional
import structlog

from .base import DomainEvent, EventEnvelope, EventBus
from .in_process import InProcessEventBus
from .redis_bus import RedisEventBus
from .registry import EventRegistry

logger = structlog.get_logger(__name__)

# Module-level singleton — RedisEventBus (in-process + cross-process via Redis pub/sub)
event_bus = RedisEventBus()


async def publish_event(
    event_type: str,
    aggregate_id: str,
    aggregate_type: str,
    payload: Dict[str, Any],
    publisher: str = "",
    correlation_id: str = "",
) -> None:
    """Publish a business event if event_bus_enabled feature flag is on.

    Usage from any component:
        from src.architecture.events import publish_event
        await publish_event("PositionClosed", ticker="BTCUSDT", ...)
    """
    from src.architecture.feature_flags import flags
    if not flags.is_enabled("event_bus_enabled"):
        return
    import uuid
    from src.metrics.crypto_metrics import record_event
    record_event(event_type)
    envelope = EventEnvelope(
        event_type=event_type,
        aggregate_id=aggregate_id,
        aggregate_type=aggregate_type,
        payload=payload,
        publisher=publisher,
        correlation_id=correlation_id or str(uuid.uuid4()),
    )
    await event_bus.publish(envelope)


__all__ = ["DomainEvent", "EventEnvelope", "EventBus", "EventRegistry", "event_bus", "publish_event"]
