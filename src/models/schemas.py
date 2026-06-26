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


class PaperSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


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


class PaperPositionCreate(BaseModel):
    """Schema for creating a paper position (Shadow Execution)."""
    signal_id: uuid.UUID
    ticker: str
    market: Market
    side: PaperSide
    quantity: Decimal = Field(..., gt=0)
    entry_price: Decimal = Field(..., gt=0)
    target_price: Decimal | None = None
    stop_loss_price: Decimal | None = None
    atr_at_entry: Decimal | None = None
    sizing_method: str = "volatility_target"
    notes: str | None = None


class PaperPositionResponse(BaseModel):
    """Schema for paper position response."""
    id: uuid.UUID
    signal_id: uuid.UUID | None
    ticker: str
    market: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal | None
    target_price: Decimal | None
    stop_loss_price: Decimal | None
    unrealized_pnl: Decimal | None
    unrealized_pnl_pct: Decimal | None
    entry_date: datetime
    notes: str | None

    class Config:
        from_attributes = True


class ClosedPaperTradeResponse(BaseModel):
    """Schema for closed paper trade response."""
    id: uuid.UUID
    ticker: str
    market: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    realized_pnl: Decimal | None
    realized_pnl_pct: Decimal | None
    entry_date: datetime | None
    exit_date: datetime
    exit_reason: str | None
    strategy: str | None
    notes: str | None

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
