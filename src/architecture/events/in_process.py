"""In-process EventBus — Phase 1 implementation.

ponytail: asyncio.Queue under the hood, no external deps.
Fire-and-forget, at-least-once delivery within the process.
Feature-flagged: event_bus_enabled (default false).
"""
from __future__ import annotations
import asyncio
from typing import Callable, Awaitable, Dict, List
import structlog
from .base import EventBus, EventEnvelope

logger = structlog.get_logger(__name__)

Handler = Callable[[EventEnvelope], Awaitable[None]]


class InProcessEventBus(EventBus):
    """Async in-process event dispatcher.

    Each event_type maps to a list of handlers.
    Handlers run concurrently via asyncio.gather.
    Errors in one handler don't block others.
    """

    def __init__(self):
        self._handlers: Dict[str, List[Handler]] = {}
        self._started = False

    async def publish(self, event: EventEnvelope) -> None:
        if not self._started:
            logger.warning("event_bus_not_started", event_type=event.event_type)
            return

        handlers = self._handlers.get(event.event_type, [])
        if not handlers:
            logger.debug("no_handlers", event_type=event.event_type)
            return

        results = await asyncio.gather(
            *(self._safe_call(h, event) for h in handlers),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.error("handler_error", event_type=event.event_type, error=str(r))

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)
        logger.debug("handler_subscribed", event_type=event_type)

    async def start(self) -> None:
        self._started = True
        logger.info("event_bus_started")

    async def stop(self) -> None:
        self._started = False
        logger.info("event_bus_stopped")

    @staticmethod
    async def _safe_call(handler: Handler, event: EventEnvelope):
        try:
            await handler(event)
        except Exception as e:
            logger.error("event_handler_failed", event_type=event.event_type, error=str(e))
            raise
