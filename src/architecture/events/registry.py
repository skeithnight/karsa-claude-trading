"""Event registry — catalog of all known business events."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class EventSchema:
    """Registered event type metadata."""
    event_type: str
    publisher: str
    subscribers: List[str]
    version: int = 1
    description: str = ""


class EventRegistry:
    """Central catalog of all business events.

    ponytail: dict lookup, no metaclass magic.
    """

    def __init__(self):
        self._events: Dict[str, EventSchema] = {}

    def register(self, schema: EventSchema):
        self._events[schema.event_type] = schema

    def get(self, event_type: str) -> Optional[EventSchema]:
        return self._events.get(event_type)

    def all_events(self) -> Dict[str, EventSchema]:
        return dict(self._events)

    def publishers_of(self, event_type: str) -> Optional[str]:
        schema = self.get(event_type)
        return schema.publisher if schema else None

    def subscribers_of(self, event_type: str) -> List[str]:
        schema = self.get(event_type)
        return schema.subscribers if schema else []
