"""Karsa Trading System — Dead Letter Queue (DLQ)

Redis-backed DLQ for failed operations that need retry:
- Failed position closes
- Failed SL/TP placements
- Failed circuit breaker activations
- Failed DB writes

Flow:
  1. Producer pushes failed operation to DLQ with metadata
  2. Consumer loop retries with exponential backoff (max 5 attempts)
  3. After max retries: alert via Telegram, keep in DLQ for manual review
"""

import asyncio
import json
from src.metrics.crypto_metrics import update_dlq_depth
import time
from typing import Any

from src.utils.logging import get_logger

logger = get_logger("dlq")

DLQ_KEY_PREFIX = "karsa:dlq"
DLQ_MAX_RETRIES = 5
DLQ_RETRY_DELAYS = [5, 15, 60, 300, 900]  # seconds: 5s, 15s, 1m, 5m, 15m


class DeadLetterQueue:
    """Redis-backed dead letter queue for failed operations."""

    def __init__(self, redis_client):
        self._redis = redis_client
        self._running = False

    async def push(self, queue_name: str, operation: dict, error: str) -> None:
        """Push a failed operation to the DLQ.

        Args:
            queue_name: e.g., "sl_placement", "position_close", "cb_activation"
            operation: The original operation dict (serializable)
            error: Error message string
        """
        entry = {
            "operation": operation,
            "error": error,
            "attempts": 0,
            "created_at": time.time(),
            "last_attempt": None,
            "next_retry": time.time() + DLQ_RETRY_DELAYS[0],
        }
        key = f"{DLQ_KEY_PREFIX}:{queue_name}"
        try:
            await self._redis.rpush(key, json.dumps(entry))
            logger.warning("dlq_pushed", queue=queue_name, error=error)
            try:
                depth = await self.get_depth(queue_name)
                update_dlq_depth(depth)
            except Exception:
                pass
        except Exception as e:
            logger.error("dlq_push_failed", queue=queue_name, error=str(e))

    async def pop_due(self, queue_name: str) -> dict | None:
        """Pop the next due item from the DLQ. Returns None if nothing due."""
        key = f"{DLQ_KEY_PREFIX}:{queue_name}"
        try:
            # Peek at the first item
            items = await self._redis.lrange(key, 0, 0)
            if not items:
                return None

            entry = json.loads(items[0])
            if entry.get("next_retry", 0) > time.time():
                return None  # Not due yet

            # Remove and return
            await self._redis.lpop(key)
            return entry
        except Exception as e:
            logger.error("dlq_pop_failed", queue=queue_name, error=str(e))
            return None

    async def requeue(self, queue_name: str, entry: dict, error: str) -> None:
        """Re-queue an item after a failed retry."""
        entry["attempts"] = entry.get("attempts", 0) + 1
        entry["last_attempt"] = time.time()
        entry["error"] = error

        if entry["attempts"] >= DLQ_MAX_RETRIES:
            # Max retries exceeded — keep in DLQ but don't auto-retry
            entry["exhausted"] = True
            key = f"{DLQ_KEY_PREFIX}:{queue_name}:exhausted"
            try:
                await self._redis.rpush(key, json.dumps(entry))
                logger.error("dlq_exhausted", queue=queue_name, attempts=entry["attempts"],
                             error=error)
            except Exception as e:
                logger.error("dlq_requeue_exhausted_failed", queue=queue_name, error=str(e))
            return

        # Schedule next retry with backoff
        delay_idx = min(entry["attempts"], len(DLQ_RETRY_DELAYS) - 1)
        entry["next_retry"] = time.time() + DLQ_RETRY_DELAYS[delay_idx]
        key = f"{DLQ_KEY_PREFIX}:{queue_name}"
        try:
            await self._redis.rpush(key, json.dumps(entry))
            logger.info("dlq_requeued", queue=queue_name, attempts=entry["attempts"],
                        next_retry_in=DLQ_RETRY_DELAYS[delay_idx])
        except Exception as e:
            logger.error("dlq_requeue_failed", queue=queue_name, error=str(e))

    async def get_depth(self, queue_name: str) -> int:
        """Get the number of items in a queue."""
        key = f"{DLQ_KEY_PREFIX}:{queue_name}"
        try:
            return await self._redis.llen(key)
        except Exception:
            return 0

    async def get_exhausted(self, queue_name: str) -> list[dict]:
        """Get items that exhausted all retries."""
        key = f"{DLQ_KEY_PREFIX}:{queue_name}:exhausted"
        try:
            items = await self._redis.lrange(key, 0, -1)
            return [json.loads(i) for i in items]
        except Exception:
            return []

    async def clear_queue(self, queue_name: str) -> int:
        """Clear a queue. Returns number of items removed."""
        key = f"{DLQ_KEY_PREFIX}:{queue_name}"
        try:
            items = await self._redis.lrange(key, 0, -1)
            if items:
                await self._redis.delete(key)
            return len(items)
        except Exception:
            return 0
