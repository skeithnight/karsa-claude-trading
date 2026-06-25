"""Karsa Trading System - Pydantic Schemas for Validation"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Market(str, Enum):
    IDX = "IDX"
    US = "US"
    ETF = "ETF"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    CLOSE = "CLOSE"


class SignalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTED = "EXECUTED"


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP = "STOP"


class TradeStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class SignalCreate(BaseModel):
    """Schema for creating a new trading signal."""
    ticker: str = Field(..., max_length=20)
    market: Market
    strategy: str = Field(..., max_length=50)
    direction: Direction
    confidence_score: int = Field(..., ge=0, le=100)
    entry_price: Decimal | None = None
    target_price: Decimal | None = None
    stop_loss_price: Decimal | None = None
    reasoning: str | None = None
    expires_in_minutes: int = Field(default=15, ge=1, le=60)

    @property
    def risk_reward_ratio(self) -> Decimal | None:
        if self.entry_price and self.target_price and self.stop_loss_price:
            risk = abs(self.entry_price - self.stop_loss_price)
            reward = abs(self.target_price - self.entry_price)
            if risk > 0:
                return round(reward / risk, 2)
        return None


class SignalResponse(BaseModel):
    """Schema for signal response."""
    id: uuid.UUID
    ticker: str
    market: str
    strategy: str
    direction: str
    confidence_score: int | None
    entry_price: Decimal | None
    target_price: Decimal | None
    stop_loss_price: Decimal | None
    risk_reward_ratio: Decimal | None
    reasoning: str | None
    status: str
    created_at: datetime
    expires_at: datetime | None

    class Config:
        from_attributes = True


class TradeCreate(BaseModel):
    """Schema for creating a trade execution."""
    signal_id: uuid.UUID
    ticker: str
    market: Market
    side: TradeSide
    quantity: Decimal = Field(..., gt=0)
    order_type: OrderType = OrderType.LIMIT
    limit_price: Decimal | None = None

    @field_validator("limit_price")
    @classmethod
    def validate_limit_price(cls, v, info):
        if info.data.get("order_type") == OrderType.LIMIT and v is None:
            raise ValueError("limit_price is required for LIMIT orders")
        return v


class TradeResponse(BaseModel):
    """Schema for trade response."""
    id: uuid.UUID
    signal_id: uuid.UUID | None
    ticker: str
    market: str
    side: str
    quantity: Decimal
    order_type: str
    limit_price: Decimal | None
    filled_price: Decimal | None
    filled_quantity: Decimal | None
    status: str
    broker_order_id: str | None
    idempotency_key: uuid.UUID
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ApprovalRequest(BaseModel):
    """Schema for trade approval request."""
    signal_id: uuid.UUID
    action: str = Field(..., pattern="^(APPROVE|REJECT|MODIFY)$")
    modification: dict | None = None


class ApprovalResponse(BaseModel):
    """Schema for approval response."""
    id: uuid.UUID
    signal_id: uuid.UUID | None
    status: str
    created_at: datetime
    expires_at: datetime

    class Config:
        from_attributes = True


class PortfolioPosition(BaseModel):
    """Schema for portfolio position."""
    ticker: str
    market: str
    quantity: Decimal
    avg_cost: Decimal
    current_price: Decimal | None
    unrealized_pnl: Decimal | None

    class Config:
        from_attributes = True


class OHLCVData(BaseModel):
    """Schema for OHLCV price data."""
    ticker: str
    market: str
    timeframe: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class MarketQuote(BaseModel):
    """Schema for real-time market quote."""
    ticker: str
    market: str
    price: Decimal
    change: Decimal
    change_pct: Decimal
    volume: int
    timestamp: datetime


class TradeAlert(BaseModel):
    """Schema for Telegram trade alert."""
    signal: SignalResponse
    position_size: Decimal
    risk_pct: Decimal
    model_used: str
    expires_in_minutes: int = 15
