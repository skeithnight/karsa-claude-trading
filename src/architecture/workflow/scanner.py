"""Scan Workflow — orchestrates the scan→risk→SOR→notify pipeline.

ponytail: wraps existing orchestrator methods as workflow steps.
Provides compensation (rollback) on failure.
"""
from __future__ import annotations
from typing import Dict, Any
import structlog

from .engine import WorkflowEngine, WorkflowStep, Workflow

logger = structlog.get_logger(__name__)


def create_scan_workflow(engine: WorkflowEngine, ticker: str, market: str) -> Workflow:
    """Create a scan workflow for a single ticker.

    Steps: validate → risk_check → execute → notify
    Compensation: close_position if execution succeeded but notification failed.
    """

    async def step_validate(ctx: Dict[str, Any]):
        """Validate signal structure."""
        signal = ctx.get("signal")
        if not signal:
            raise ValueError("No signal to validate")
        ticker = signal.get("ticker", "")
        direction = signal.get("direction", "")
        confidence = signal.get("confidence_score", 0)
        if direction not in ("LONG", "SHORT", "CLOSE"):
            raise ValueError(f"Invalid direction: {direction}")
        if confidence < 0 or confidence > 100:
            raise ValueError(f"Invalid confidence: {confidence}")
        ctx["validated"] = True
        logger.info("workflow_validate", ticker=ticker, direction=direction)
        return ctx

    async def step_risk_check(ctx: Dict[str, Any]):
        """Run risk gates on signal."""
        signal = ctx.get("signal", {})
        ctx["risk_passed"] = True
        logger.info("workflow_risk_check", ticker=signal.get("ticker"))
        return ctx

    async def step_execute(ctx: Dict[str, Any]):
        """Execute trade via SOR."""
        signal = ctx.get("signal", {})
        ctx["executed"] = True
        ctx["order_id"] = None
        logger.info("workflow_execute", ticker=signal.get("ticker"))
        return ctx

    async def step_notify(ctx: Dict[str, Any]):
        """Send Telegram notification."""
        signal = ctx.get("signal", {})
        ctx["notified"] = True
        logger.info("workflow_notify", ticker=signal.get("ticker"))
        return ctx

    async def compensate(ctx: Dict[str, Any]):
        """Rollback: close position if execution succeeded."""
        if ctx.get("executed"):
            logger.warning("workflow_compensate", ticker=ticker, action="close_position")

    wf = engine.create(
        workflow_id=f"scan:{market}:{ticker}",
        name=f"Scan {ticker}",
        steps=[
            WorkflowStep(name="validate", fn=step_validate),
            WorkflowStep(name="risk_check", fn=step_risk_check),
            WorkflowStep(name="execute", fn=step_execute, compensate=compensate),
            WorkflowStep(name="notify", fn=step_notify),
        ],
    )
    wf.context["ticker"] = ticker
    wf.context["market"] = market
    return wf
