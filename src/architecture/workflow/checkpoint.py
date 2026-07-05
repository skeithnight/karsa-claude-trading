"""Checkpoint manager for workflow crash recovery.

ponytail: in-memory dict. Production: Redis-backed with TTL.
"""
from __future__ import annotations
from typing import Any, Dict, Optional
import json
import structlog

logger = structlog.get_logger(__name__)


class CheckpointManager:
    """Persists workflow state for crash recovery."""

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._local: Dict[str, dict] = {}
        self._prefix = "karsa:workflow:checkpoint"

    def save(self, workflow_id: str, step_index: int, context: dict):
        data = {"step_index": step_index, "context": context}
        self._local[workflow_id] = data
        if self._redis:
            self._redis.set(f"{self._prefix}:{workflow_id}", json.dumps(data, default=str))

    def load(self, workflow_id: str) -> Optional[dict]:
        if self._redis:
            raw = self._redis.get(f"{self._prefix}:{workflow_id}")
            if raw:
                return json.loads(raw)
        return self._local.get(workflow_id)

    def clear(self, workflow_id: str):
        self._local.pop(workflow_id, None)
        if self._redis:
            self._redis.delete(f"{self._prefix}:{workflow_id}")
