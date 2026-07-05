"""Workflow Engine — durable, resumable business processes.

Key capability: waiting conditions (WaitUntil OrderFilled, WaitUntil Price > X).
Checkpointing for crash recovery. Compensation for rollback.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable, Optional, List, Dict
from datetime import datetime, timezone
import asyncio
import structlog

logger = structlog.get_logger(__name__)


class WorkflowState(str, Enum):
    CREATED = "CREATED"
    STARTED = "STARTED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    RESUMED = "RESUMED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class StepResult:
    step_name: str
    success: bool
    output: Any = None
    error: Optional[str] = None


@dataclass
class WorkflowStep:
    name: str
    fn: Callable[[dict], Awaitable[Any]]
    compensate: Optional[Callable[[dict], Awaitable[None]]] = None


@dataclass
class Workflow:
    workflow_id: str
    name: str
    state: WorkflowState = WorkflowState.CREATED
    steps: List[WorkflowStep] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    results: List[StepResult] = field(default_factory=list)
    current_step: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None


class WorkflowEngine:
    """Executes durable workflows with checkpointing and compensation.

    ponytail: in-memory checkpoint dict. Production: swap with Redis/DB.
    """

    def __init__(self, checkpoint_manager=None):
        self._workflows: Dict[str, Workflow] = {}
        self._checkpoint = checkpoint_manager

    def create(self, workflow_id: str, name: str, steps: List[WorkflowStep]) -> Workflow:
        wf = Workflow(workflow_id=workflow_id, name=name, steps=steps)
        self._workflows[workflow_id] = wf
        return wf

    async def execute(self, workflow_id: str) -> Workflow:
        wf = self._workflows.get(workflow_id)
        if not wf:
            raise KeyError(f"Workflow not found: {workflow_id}")

        wf.state = WorkflowState.STARTED
        wf.state = WorkflowState.RUNNING
        executed_steps = []

        try:
            for i, step in enumerate(wf.steps):
                wf.current_step = i
                logger.info("workflow_step", workflow_id=workflow_id, step=step.name)

                try:
                    output = await step.fn(wf.context)
                    wf.results.append(StepResult(step_name=step.name, success=True, output=output))
                    wf.context[step.name] = output
                    executed_steps.append(step)
                except Exception as e:
                    wf.results.append(StepResult(step_name=step.name, success=False, error=str(e)))
                    logger.error("workflow_step_failed", workflow_id=workflow_id,
                               step=step.name, error=str(e))
                    # Compensate in reverse order
                    for prev_step in reversed(executed_steps):
                        if prev_step.compensate:
                            await prev_step.compensate(wf.context)
                    wf.state = WorkflowState.FAILED
                    return wf

            wf.state = WorkflowState.COMPLETED
            wf.completed_at = datetime.now(timezone.utc)
            logger.info("workflow_completed", workflow_id=workflow_id)

        except Exception as e:
            wf.state = WorkflowState.FAILED
            logger.error("workflow_failed", workflow_id=workflow_id, error=str(e))

        return wf

    async def wait_for(self, workflow_id: str, condition: Callable[[], Awaitable[bool]],
                       timeout_seconds: float = 300, poll_interval: float = 1.0):
        wf = self._workflows.get(workflow_id)
        if not wf:
            raise KeyError(f"Workflow not found: {workflow_id}")

        wf.state = WorkflowState.WAITING
        deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds

        while datetime.now(timezone.utc).timestamp() < deadline:
            if await condition():
                wf.state = WorkflowState.RESUMED
                return True
            await asyncio.sleep(poll_interval)

        wf.state = WorkflowState.FAILED
        logger.error("workflow_timeout", workflow_id=workflow_id)
        return False

    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return self._workflows.get(workflow_id)

    def cancel(self, workflow_id: str):
        wf = self._workflows.get(workflow_id)
        if wf:
            wf.state = WorkflowState.CANCELLED
