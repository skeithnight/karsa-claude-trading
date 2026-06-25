"""Karsa Trading System - Abstract Broker Interface"""

import uuid
from abc import ABC, abstractmethod
from decimal import Decimal

from src.utils.logging import get_logger

logger = get_logger("broker")


class BaseBroker(ABC):
    """Abstract base class for broker API integrations."""

    def __init__(self, market: str):
        self.market = market

    @abstractmethod
    async def place_order(self, ticker: str, side: str, quantity: Decimal,
                          order_type: str = "LIMIT", limit_price: Decimal | None = None,
                          idempotency_key: uuid.UUID | None = None) -> dict:
        """Returns: {"broker_order_id": str, "status": str, "filled_price": float | None}"""
        ...

    @abstractmethod
    async def get_order_status(self, broker_order_id: str) -> dict:
        """Returns: {"broker_order_id": str, "status": str, "filled_quantity": float, "filled_price": float}"""
        ...

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> dict:
        ...

    @abstractmethod
    async def get_positions(self) -> list[dict]:
        """Returns: [{"ticker": str, "quantity": float, "avg_cost": float, "current_price": float}]"""
        ...

    @abstractmethod
    async def get_balance(self) -> dict:
        """Returns: {"cash": float, "equity": float, "buying_power": float}"""
        ...

    async def close(self):
        pass
