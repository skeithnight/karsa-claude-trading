"""Event contracts — immutable business events with standard envelope."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
import uuid


@dataclass(frozen=True)
class EventEnvelope:
    """Standard event envelope — every business event shares this metadata."""
    event_type: str
    aggregate_id: str
    aggregate_type: str
    payload: dict
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_version: int = 1
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    publisher: str = ""
    correlation_id: str = ""
    causation_id: str = ""


@dataclass(frozen=True)
class DomainEvent:
    """Business event wrapper — fact that already happened."""
    envelope: EventEnvelope
    data: Any = None


class EventBus(ABC):
    """Event bus interface — publish/subscribe for business events."""

    @abstractmethod
    async def publish(self, event: EventEnvelope) -> None: ...

    @abstractmethod
    def subscribe(self, event_type: str, handler: Callable[[EventEnvelope], Awaitable[None]]) -> None: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
