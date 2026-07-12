"""Agent Runtime — lifecycle management for AI agents.

State machine: CREATED→INITIALIZING→READY→RUNNING→COMPLETED/FAILED→RETRYING→READY

FATAL errors (TypeError, ValueError, auth failures) skip retries —
they will never self-heal. TRANSIENT errors (ConnectionError, timeout) retry normally.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Callable, Awaitable
from datetime import datetime, timezone
import asyncio
import structlog

from src.utils.error_classification import classify_error, ErrorSeverity

logger = structlog.get_logger(__name__)

class AgentState(str, Enum):
    CREATED = "CREATED"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"

_TRANSITIONS = {
    AgentState.CREATED: {AgentState.INITIALIZING},
    AgentState.INITIALIZING: {AgentState.READY, AgentState.FAILED},
    AgentState.READY: {AgentState.RUNNING},
    AgentState.RUNNING: {AgentState.COMPLETED, AgentState.FAILED},
    AgentState.FAILED: {AgentState.RETRYING, AgentState.COMPLETED},
    AgentState.RETRYING: {AgentState.READY},
    AgentState.COMPLETED: set(),
}

@dataclass
class AgentRun:
    agent_id: str
    agent_type: str
    state: AgentState = AgentState.CREATED
    result: Any = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

class AgentRuntime:
    """Manages agent lifecycle — create, run, retry, complete.

    ponytail: wraps async callables, tracks state per agent_id.
    """

    def __init__(self, max_concurrent: int = 5):
        self._agents: dict[str, AgentRun] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def _transition(self, run: AgentRun, target: AgentState):
        allowed = _TRANSITIONS.get(run.state, set())
        if target not in allowed:
            raise ValueError(f"Agent {run.agent_id}: invalid transition {run.state} -> {target}")
        run.state = target

    async def execute(self, agent_id: str, agent_type: str,
                      fn: Callable[[], Awaitable[Any]], max_retries: int = 3) -> AgentRun:
        run = AgentRun(agent_id=agent_id, agent_type=agent_type, max_retries=max_retries)
        self._agents[agent_id] = run

        self._transition(run, AgentState.INITIALIZING)
        self._transition(run, AgentState.READY)

        async with self._semaphore:
            while run.retry_count <= max_retries:
                try:
                    run.started_at = datetime.now(timezone.utc)
                    self._transition(run, AgentState.RUNNING)
                    run.result = await fn()
                    self._transition(run, AgentState.COMPLETED)
                    run.completed_at = datetime.now(timezone.utc)
                    logger.info("agent_completed", agent_id=agent_id, retries=run.retry_count)
                    return run
                except Exception as e:
                    run.error = str(e)
                    severity = classify_error(e)
                    logger.error("agent_failed", agent_id=agent_id, error=str(e),
                                 retry=run.retry_count, severity=severity.value)

                    # FATAL errors (config/code bugs) — never retry
                    if severity == ErrorSeverity.FATAL:
                        logger.error("agent_fatal_no_retry", agent_id=agent_id, error=str(e))
                        self._transition(run, AgentState.FAILED)
                        return run

                    if run.retry_count < max_retries:
                        self._transition(run, AgentState.FAILED)
                        run.retry_count += 1
                        self._transition(run, AgentState.RETRYING)
                        # Exponential backoff: min(2^attempt * 1s, 10s) + jitter
                        import random
                        delay = min(2 ** run.retry_count, 10) + random.uniform(0, 1)
                        logger.info("agent_retry_backoff", agent_id=agent_id,
                                    attempt=run.retry_count, delay=round(delay, 1))
                        await asyncio.sleep(delay)
                        self._transition(run, AgentState.READY)
                    else:
                        self._transition(run, AgentState.FAILED)
                        return run
        return run
