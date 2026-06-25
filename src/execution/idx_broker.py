"""Karsa Trading System - IDX Broker (IPOT/Mirae) Adapter"""

import uuid
from decimal import Decimal

import httpx

from src.config import settings
from src.execution.base import BaseBroker
from src.utils.logging import get_logger

logger = get_logger("idx_broker")


class IDXBroker(BaseBroker):
    """IDX broker adapter. Enforces lot size (100 shares) and ARA limits."""

    LOT_SIZE = 100

    def __init__(self):
        super().__init__(market="IDX")
        self.base_url = settings.IDX_BROKER_API_URL
        self.client = httpx.AsyncClient(
            timeout=30.0, headers={"Authorization": f"Bearer {settings.IDX_BROKER_TOKEN}"})

    async def close(self):
        await self.client.aclose()

    async def place_order(self, ticker: str, side: str, quantity: Decimal,
                          order_type: str = "LIMIT", limit_price: Decimal | None = None,
                          idempotency_key: uuid.UUID | None = None) -> dict:
        lots = int(quantity) // self.LOT_SIZE
        if lots < 1:
            return {"broker_order_id": None, "status": "REJECTED",
                    "filled_price": None, "reason": f"Qty {quantity} < 1 lot"}

        actual_qty = lots * self.LOT_SIZE
        if actual_qty != int(quantity):
            logger.info("lot_adjusted", ticker=ticker, requested=int(quantity), actual=actual_qty)

        payload = {"symbol": ticker, "side": side, "quantity": actual_qty,
                   "order_type": order_type, "price": float(limit_price) if limit_price else None,
                   "idempotency_key": str(idempotency_key or uuid.uuid4())}
        try:
            resp = await self.client.post(f"{self.base_url}/orders", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"broker_order_id": data.get("order_id"), "status": data.get("status", "PENDING"),
                    "filled_price": data.get("filled_price")}
        except httpx.HTTPStatusError as e:
            body = e.response.json() if "json" in e.response.headers.get("content-type", "") else {}
            logger.error("idx_order_rejected", ticker=ticker, status=e.response.status_code)
            return {"broker_order_id": None, "status": "REJECTED",
                    "filled_price": None, "reason": body.get("message", str(e))}
        except Exception as e:
            logger.error("idx_order_error", ticker=ticker, error=str(e))
            return {"broker_order_id": None, "status": "REJECTED", "filled_price": None, "reason": str(e)}

    async def get_order_status(self, broker_order_id: str) -> dict:
        try:
            resp = await self.client.get(f"{self.base_url}/orders/{broker_order_id}")
            resp.raise_for_status()
            d = resp.json()
            return {"broker_order_id": broker_order_id, "status": d.get("status", "PENDING"),
                    "filled_quantity": float(d.get("filled_quantity", 0)),
                    "filled_price": float(d.get("filled_price", 0))}
        except Exception as e:
            logger.error("idx_status_error", order_id=broker_order_id, error=str(e))
            return {"broker_order_id": broker_order_id, "status": "UNKNOWN",
                    "filled_quantity": 0, "filled_price": 0}

    async def cancel_order(self, broker_order_id: str) -> dict:
        try:
            resp = await self.client.delete(f"{self.base_url}/orders/{broker_order_id}")
            resp.raise_for_status()
            return {"broker_order_id": broker_order_id, "status": "CANCELLED"}
        except Exception as e:
            logger.error("idx_cancel_error", order_id=broker_order_id, error=str(e))
            return {"broker_order_id": broker_order_id, "status": "CANCEL_FAILED", "reason": str(e)}

    async def get_positions(self) -> list[dict]:
        try:
            resp = await self.client.get(f"{self.base_url}/positions")
            resp.raise_for_status()
            return resp.json().get("positions", [])
        except Exception as e:
            logger.error("idx_positions_error", error=str(e))
            return []

    async def get_balance(self) -> dict:
        try:
            resp = await self.client.get(f"{self.base_url}/account/balance")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("idx_balance_error", error=str(e))
            return {"cash": 0, "equity": 0, "buying_power": 0}
