"""Karsa Trading System — Order Management System (OMS)

Tracks orders through their lifecycle:
  NEW → SUBMITTED → PARTIAL → FILLED / CANCELLED / REJECTED

Redis-backed state machine. No DB migration needed.
Also handles stuck order cleanup (cancel limit orders >15min unfilled).

Flow:
  SOR calls oms.track_order() when placing →
  SOR calls oms.update_status() on fill/cancel →
  APScheduler calls oms.cleanup_stuck_orders() every 2 min.
"""

import json
import time

from src.utils.logging import get_logger

logger = get_logger("oms")

REDIS_ORDER_PREFIX = "karsa:oms:order"
REDIS_ORDER_SET = "karsa:oms:active_orders"
ORDER_STALE_SEC = 15 * 60  # 15 minutes

VALID_TRANSITIONS = {
    "NEW": {"SUBMITTED", "CANCELLED", "REJECTED"},
    "SUBMITTED": {"PARTIAL", "FILLED", "CANCELLED", "REJECTED"},
    "PARTIAL": {"PARTIAL", "FILLED", "CANCELLED", "REJECTED"},
}


class OrderManagementSystem:
    """Tracks order lifecycle and cleans up stuck orders."""

    def __init__(self, redis_client, bybit_client):
        self._redis = redis_client
        self._bybit = bybit_client

    async def track_order(self, order_id: str, ticker: str, side: str,
                          quantity: float, order_type: str, **kwargs) -> dict:
        """Register a new order in the OMS."""
        order = {
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
            "status": "SUBMITTED",
            "filled_qty": 0,
            "avg_fill_price": 0,
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
            **kwargs,
        }

        await self._redis.setex(
            f"{REDIS_ORDER_PREFIX}:{order_id}", 3600, json.dumps(order),
        )
        await self._redis.sadd(REDIS_ORDER_SET, order_id)

        logger.info("order_tracked", order_id=order_id, ticker=ticker,
                    side=side, qty=quantity, type=order_type)
        return order

    async def update_status(self, order_id: str, new_status: str,
                             filled_qty: float = 0, avg_price: float = 0) -> bool:
        """Update order status with transition validation."""
        raw = await self._redis.get(f"{REDIS_ORDER_PREFIX}:{order_id}")
        if not raw:
            logger.warning("order_not_found", order_id=order_id)
            return False

        order = json.loads(raw)
        current = order.get("status", "")

        if current != new_status:
            allowed = VALID_TRANSITIONS.get(current, set())
            if new_status not in allowed:
                logger.warning("invalid_order_transition",
                             order_id=order_id, from_status=current, to_status=new_status)
                return False

        order["status"] = new_status
        order["updated_at"] = int(time.time())
        if filled_qty:
            order["filled_qty"] = filled_qty
        if avg_price:
            order["avg_fill_price"] = avg_price

        await self._redis.setex(
            f"{REDIS_ORDER_PREFIX}:{order_id}", 3600, json.dumps(order),
        )

        if new_status in ("FILLED", "CANCELLED", "REJECTED"):
            await self._redis.srem(REDIS_ORDER_SET, order_id)

        logger.info("order_status_updated", order_id=order_id,
                    status=new_status, filled_qty=filled_qty)
        return True

    async def get_order(self, order_id: str) -> dict | None:
        """Get order state."""
        raw = await self._redis.get(f"{REDIS_ORDER_PREFIX}:{order_id}")
        return json.loads(raw) if raw else None

    async def get_active_orders(self) -> list[dict]:
        """Get all active (non-terminal) orders."""
        order_ids = await self._redis.smembers(REDIS_ORDER_SET)
        orders = []
        for oid in order_ids:
            order = await self.get_order(oid)
            if order:
                orders.append(order)
            else:
                await self._redis.srem(REDIS_ORDER_SET, oid)
        return orders

    async def cleanup_stuck_orders(self) -> list[dict]:
        """Cancel limit orders that have been open >15 minutes."""
        now = int(time.time())
        stuck = []

        orders = await self.get_active_orders()
        for order in orders:
            age = now - order.get("created_at", now)
            if age < ORDER_STALE_SEC:
                continue
            if order.get("order_type", "").lower() not in ("limit", "limit_maker"):
                continue

            order_id = order["order_id"]
            ticker = order.get("ticker", "")

            try:
                result = await self._bybit.cancel_order(
                    symbol=ticker, order_id=order_id,
                )
                if result:
                    await self.update_status(order_id, "CANCELLED")
                    stuck.append(order)
                    logger.info("stuck_order_cancelled",
                              order_id=order_id, ticker=ticker, age_sec=age)
            except Exception as e:
                logger.error("stuck_order_cancel_failed",
                           order_id=order_id, error=str(e))

        if stuck:
            logger.info("stuck_orders_cleanup", count=len(stuck))
        return stuck

    async def sync_from_exchange(self) -> None:
        """Reconcile OMS state with exchange orders."""
        try:
            active = await self.get_active_orders()
            for order in active:
                order_id = order["order_id"]
                ticker = order.get("ticker", "")

                try:
                    resp = await self._bybit.get_order_status(
                        symbol=ticker, order_id=order_id,
                    )
                    if not resp or resp.get("error"):
                        continue

                    exchange_status = resp.get("status", "")
                    status_map = {
                        "Filled": "FILLED",
                        "Cancelled": "CANCELLED",
                        "Rejected": "REJECTED",
                        "PartiallyFilled": "PARTIAL",
                        "New": "SUBMITTED",
                        "Untriggered": "SUBMITTED",
                    }
                    new_status = status_map.get(exchange_status)
                    if new_status and new_status != order.get("status"):
                        filled = float(resp.get("filled_qty", 0) or 0)
                        avg = float(resp.get("avg_price", 0) or 0)
                        await self.update_status(order_id, new_status, filled, avg)
                except Exception:
                    pass
        except Exception as e:
            logger.error("oms_exchange_sync_failed", error=str(e))
