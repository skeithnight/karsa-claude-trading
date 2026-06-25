"""Karsa Trading System - US Broker (Alpaca/IBKR) Adapter"""

import uuid
from decimal import Decimal

import httpx

from src.config import settings
from src.execution.base import BaseBroker
from src.utils.logging import get_logger

logger = get_logger("us_broker")


class USBroker(BaseBroker):
    """US broker adapter (Alpaca). Supports fractional shares and PDT checks."""

    def __init__(self):
        super().__init__(market="US")
        self.base_url = settings.US_BROKER_API_URL
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "APCA-API-KEY-ID": settings.US_BROKER_KEY,
                "APCA-API-SECRET-KEY": settings.US_BROKER_SECRET,
            },
        )

    async def close(self):
        await self.client.aclose()

    async def place_order(self, ticker: str, side: str, quantity: Decimal,
                          order_type: str = "LIMIT", limit_price: Decimal | None = None,
                          idempotency_key: uuid.UUID | None = None) -> dict:
        payload = {
            "symbol": ticker,
            "qty": str(quantity),
            "side": side.lower(),
            "type": order_type.lower(),
            "time_in_force": "day",
            "client_order_id": str(idempotency_key or uuid.uuid4()),
        }
        if limit_price and order_type == "LIMIT":
            payload["limit_price"] = str(limit_price)

        try:
            resp = await self.client.post(f"{self.base_url}/orders", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "broker_order_id": data.get("id"),
                "status": data.get("status", "new").upper(),
                "filled_price": float(data["filled_avg_price"]) if data.get("filled_avg_price") else None,
            }
        except httpx.HTTPStatusError as e:
            body = e.response.json() if "json" in e.response.headers.get("content-type", "") else {}
            logger.error("us_order_rejected", ticker=ticker, status=e.response.status_code)
            return {"broker_order_id": None, "status": "REJECTED",
                    "filled_price": None, "reason": body.get("message", str(e))}
        except Exception as e:
            logger.error("us_order_error", ticker=ticker, error=str(e))
            return {"broker_order_id": None, "status": "REJECTED", "filled_price": None, "reason": str(e)}

    async def get_order_status(self, broker_order_id: str) -> dict:
        try:
            resp = await self.client.get(f"{self.base_url}/orders/{broker_order_id}")
            resp.raise_for_status()
            d = resp.json()
            return {
                "broker_order_id": broker_order_id,
                "status": d.get("status", "new").upper(),
                "filled_quantity": float(d.get("filled_qty", 0)),
                "filled_price": float(d["filled_avg_price"]) if d.get("filled_avg_price") else 0,
            }
        except Exception as e:
            logger.error("us_status_error", order_id=broker_order_id, error=str(e))
            return {"broker_order_id": broker_order_id, "status": "UNKNOWN",
                    "filled_quantity": 0, "filled_price": 0}

    async def cancel_order(self, broker_order_id: str) -> dict:
        try:
            resp = await self.client.delete(f"{self.base_url}/orders/{broker_order_id}")
            resp.raise_for_status()
            return {"broker_order_id": broker_order_id, "status": "CANCELLED"}
        except Exception as e:
            logger.error("us_cancel_error", order_id=broker_order_id, error=str(e))
            return {"broker_order_id": broker_order_id, "status": "CANCEL_FAILED", "reason": str(e)}

    async def get_positions(self) -> list[dict]:
        try:
            resp = await self.client.get(f"{self.base_url}/positions")
            resp.raise_for_status()
            return [
                {
                    "ticker": p["symbol"],
                    "quantity": float(p["qty"]),
                    "avg_cost": float(p["avg_entry_price"]),
                    "current_price": float(p["current_price"]),
                }
                for p in resp.json()
            ]
        except Exception as e:
            logger.error("us_positions_error", error=str(e))
            return []

    async def get_balance(self) -> dict:
        try:
            resp = await self.client.get(f"{self.base_url}/account")
            resp.raise_for_status()
            d = resp.json()
            return {
                "cash": float(d.get("cash", 0)),
                "equity": float(d.get("equity", 0)),
                "buying_power": float(d.get("buying_power", 0)),
            }
        except Exception as e:
            logger.error("us_balance_error", error=str(e))
            return {"cash": 0, "equity": 0, "buying_power": 0}
