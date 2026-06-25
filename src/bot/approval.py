"""Karsa Trading System - HITL Approval Flow

Signal lifecycle:
  1. Orchestrator generates signal → inserts to `signals` table (PENDING)
  2. Publishes to Redis channel `karsa:signals`
  3. Telegram bot picks up, sends alert with APPROVE/REJECT buttons
  4. User clicks APPROVE → bot publishes to Redis channel `karsa:approvals`
  5. Orchestrator picks up approval → executes via broker
  6. Updates `trades` table, logs to `audit_logs`
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.cache import CacheManager
from src.models.tables import Signal, Trade, PendingApproval, AuditLog
from src.execution.base import BaseBroker
from src.utils.logging import get_logger

logger = get_logger("approval")


class ApprovalManager:
    """Manages the HITL trade approval lifecycle."""

    def __init__(self, cache: CacheManager, session_factory):
        self.cache = cache
        self.session_factory = session_factory

    async def create_pending_approval(
        self, signal_id: uuid.UUID, telegram_message_id: int, expires_minutes: int = 15
    ) -> uuid.UUID:
        async with self.session_factory() as session:
            approval = PendingApproval(
                signal_id=signal_id,
                telegram_message_id=telegram_message_id,
                status="WAITING",
                expires_at=datetime.utcnow() + timedelta(minutes=expires_minutes),
            )
            session.add(approval)
            await session.commit()
            logger.info("approval_created", signal_id=str(signal_id), approval_id=str(approval.id))
            return approval.id

    async def process_approval(
        self, signal_id: str, action: str, broker: BaseBroker, modification: dict | None = None
    ) -> dict:
        async with self.session_factory() as session:
            result = await session.execute(
                select(PendingApproval).where(
                    PendingApproval.signal_id == uuid.UUID(signal_id),
                    PendingApproval.status == "WAITING",
                )
            )
            approval = result.scalar_one_or_none()

            if not approval:
                return {"error": "No pending approval found", "signal_id": signal_id}

            if datetime.utcnow() > approval.expires_at:
                approval.status = "EXPIRED"
                await session.commit()
                return {"error": "Approval expired", "signal_id": signal_id}

            sig_result = await session.execute(
                select(Signal).where(Signal.id == uuid.UUID(signal_id))
            )
            signal = sig_result.scalar_one_or_none()
            if not signal:
                return {"error": "Signal not found", "signal_id": signal_id}

            if action == "APPROVE":
                approval.status = "APPROVED"
                approval.responded_at = datetime.utcnow()
                signal.status = "APPROVED"

                trade_result = await self._execute_trade(signal, broker, session)

                await self.cache.publish_approval({
                    "signal_id": signal_id,
                    "action": "APPROVED",
                    "trade_result": trade_result,
                })

                await session.commit()
                return {"status": "approved", "trade": trade_result}

            elif action == "REJECT":
                approval.status = "REJECTED"
                approval.responded_at = datetime.utcnow()
                signal.status = "REJECTED"
                session.add(AuditLog(
                    component="TELEGRAM", action="SIGNAL_REJECTED",
                    entity_type="signal", entity_id=signal.id,
                    payload={"reason": "User rejected via Telegram"}
                ))
                await session.commit()
                return {"status": "rejected"}

            elif action == "MODIFY":
                approval.status = "MODIFIED"
                approval.modification = modification
                approval.responded_at = datetime.utcnow()
                await session.commit()
                # Publish the modification back for the orchestrator to re-run risk checks
                # and generate a new pending approval
                await self.cache.publish_approval({
                    "signal_id": signal_id,
                    "action": "MODIFIED",
                    "modification": modification,
                })
                return {"status": "modified", "modification": modification}

            return {"error": f"Unknown action: {action}"}

    async def _execute_trade(self, signal: Signal, broker: BaseBroker, session: AsyncSession) -> dict:
        idempotency_key = uuid.uuid4()
        # Extract adjusted quantity from the risk manager output
        # Risk manager injects this into the signal payload.
        # Fallback to 1 for MVP if missing.
        risk_check = getattr(signal, "payload", {}).get("risk_check", {}) if hasattr(signal, "payload") else {}
        quantity = risk_check.get("adjusted_quantity") or getattr(signal, "quantity", 1) or 1

        trade = Trade(
            signal_id=signal.id,
            ticker=signal.ticker,
            market=signal.market,
            side="BUY" if signal.direction == "LONG" else "SELL",
            quantity=quantity,
            order_type="LIMIT",
            limit_price=signal.entry_price,
            idempotency_key=idempotency_key,
        )
        session.add(trade)
        await session.flush()

        result = await broker.place_order(
            ticker=signal.ticker,
            side=trade.side,
            quantity=trade.quantity,
            order_type=trade.order_type,
            limit_price=trade.limit_price,
            idempotency_key=idempotency_key,
        )

        trade.broker_order_id = result.get("broker_order_id")
        trade.status = result.get("status", "PENDING")
        trade.filled_price = result.get("filled_price")
        trade.rejection_reason = result.get("reason")

        signal.status = "REJECTED" if trade.status == "REJECTED" else "EXECUTED"

        session.add(AuditLog(
            component="BROKER", action="TRADE_EXECUTED",
            entity_type="trade", entity_id=trade.id,
            payload={"signal_id": str(signal.id), "broker_order_id": trade.broker_order_id, "status": trade.status}
        ))
        logger.info("trade_executed", ticker=signal.ticker, status=trade.status)
        return {
            "trade_id": str(trade.id), "broker_order_id": trade.broker_order_id,
            "status": trade.status, "rejection_reason": trade.rejection_reason,
        }

    async def expire_stale_approvals(self):
        async with self.session_factory() as session:
            now = datetime.utcnow()
            result = await session.execute(
                select(PendingApproval).where(
                    PendingApproval.status == "WAITING",
                    PendingApproval.expires_at < now,
                )
            )
            stale = result.scalars().all()
            for approval in stale:
                approval.status = "EXPIRED"
                await session.execute(
                    update(Signal).where(Signal.id == approval.signal_id).values(status="EXPIRED")
                )
            if stale:
                await session.commit()
                logger.info("approvals_expired", count=len(stale))
