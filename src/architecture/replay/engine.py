"""Replay Engine — deterministic reconstruction of trading decisions.

Never modifies state. Side-effect free. Parses event history.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime
import structlog

from ..events.base import EventEnvelope

logger = structlog.get_logger(__name__)


@dataclass
class ReplayResult:
    aggregate_id: str
    events: List[EventEnvelope] = field(default_factory=list)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    divergences: List[str] = field(default_factory=list)


class ReplayEngine:
    """Deterministic replay of business events for a given aggregate.

    ponytail: stores events in-memory list, replays by filtering on aggregate_id.
    Production: swap list with DB-backed event store.
    """

    def __init__(self):
        self._event_store: List[EventEnvelope] = []

    def store_event(self, event: EventEnvelope):
        self._event_store.append(event)

    def store_events(self, events: List[EventEnvelope]):
        self._event_store.extend(events)

    def replay(self, aggregate_id: str) -> ReplayResult:
        events = [e for e in self._event_store if e.aggregate_id == aggregate_id]
        events.sort(key=lambda e: e.timestamp)

        timeline = []
        for ev in events:
            timeline.append({
                "event_type": ev.event_type,
                "timestamp": ev.timestamp.isoformat(),
                "publisher": ev.publisher,
                "payload": ev.payload,
            })

        logger.info("replay_completed", aggregate_id=aggregate_id, event_count=len(events))
        return ReplayResult(aggregate_id=aggregate_id, events=events, timeline=timeline)

    def replay_all(self, aggregate_type: str = "") -> List[ReplayResult]:
        if aggregate_type:
            ids = {e.aggregate_id for e in self._event_store if e.aggregate_type == aggregate_type}
        else:
            ids = {e.aggregate_id for e in self._event_store}
        return [self.replay(aid) for aid in ids]

    def compare(self, result: ReplayResult, expected_timeline: List[Dict]) -> List[str]:
        """Compare replay result against expected timeline."""
        divergences = []
        for i, (actual, expected) in enumerate(zip(result.timeline, expected_timeline)):
            if actual["event_type"] != expected.get("event_type"):
                divergences.append(f"Step {i}: expected {expected.get('event_type')}, got {actual['event_type']}")
        if len(result.timeline) != len(expected_timeline):
            divergences.append(f"Length mismatch: {len(result.timeline)} vs {len(expected_timeline)}")
        return divergences

    def clear(self):
        self._event_store.clear()
