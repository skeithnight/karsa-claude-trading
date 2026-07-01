"""Karsa Trading System — Liquidity Monitor & Slippage Estimator

Pre-trade checks for orderbook depth and spread.
Used by SOR before market orders and by /liquidity Telegram command.

Components:
- LiquidityMonitor: checks spread and depth, returns can_trade verdict
- SlippageEstimator: simulates order fill through orderbook levels

Flow:
  SOR calls check_liquidity() before market order →
  If spread too wide or depth too thin → reject order →
  SlippageEstimator.estimate_slippage() → pre-trade cost estimate.
"""

import asyncio
from decimal import Decimal
from typing import Optional

from src.utils.logging import get_logger

logger = get_logger("liquidity")

# Thresholds
MIN_ORDER_BOOK_DEPTH_USD = 100_000  # $100k within 0.5% of mid
MAX_SPREAD_PCT = Decimal("0.002")   # 0.2% max bid-ask spread
MAX_SLIPPAGE_PCT = Decimal("0.005") # 0.5% max acceptable slippage
DEPTH_LEVELS = 10                   # top N orderbook levels to check


class LiquidityMonitor:
    """Checks orderbook spread and depth before trade execution."""

    def __init__(self, bybit):
        self.bybit = bybit

    async def check_liquidity(self, ticker: str, side: str, size_usd: float = 0) -> dict:
        """Check if liquidity is sufficient for a trade.

        Args:
            ticker: Symbol (e.g. "BTCUSDT")
            side: "BUY" or "SELL"
            size_usd: Order size in USD (optional, for depth check)

        Returns:
            {"can_trade": bool, "reason": str, "spread_pct": float, "depth_usd": float}
        """
        try:
            orderbook = await self._get_orderbook(ticker)
            if not orderbook:
                return {"can_trade": False, "reason": "orderbook_fetch_failed",
                        "spread_pct": 0, "depth_usd": 0}

            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])

            if not bids or not asks:
                return {"can_trade": False, "reason": "empty_orderbook",
                        "spread_pct": 0, "depth_usd": 0}

            # Calculate spread
            best_bid = Decimal(str(bids[0][0]))
            best_ask = Decimal(str(asks[0][0]))
            mid_price = (best_bid + best_ask) / 2
            spread_pct = (best_ask - best_bid) / mid_price

            if spread_pct > MAX_SPREAD_PCT:
                return {
                    "can_trade": False,
                    "reason": f"spread_too_wide:{float(spread_pct):.4%}",
                    "spread_pct": round(float(spread_pct), 6),
                    "depth_usd": 0,
                }

            # Calculate depth (USD value of top N levels on relevant side)
            if side.upper() == "BUY":
                depth_usd = sum(
                    Decimal(str(level[0])) * Decimal(str(level[1]))
                    for level in asks[:DEPTH_LEVELS]
                )
            else:
                depth_usd = sum(
                    Decimal(str(level[0])) * Decimal(str(level[1]))
                    for level in bids[:DEPTH_LEVELS]
                )

            if depth_usd < MIN_ORDER_BOOK_DEPTH_USD:
                return {
                    "can_trade": False,
                    "reason": f"insufficient_depth:${float(depth_usd):,.0f}",
                    "spread_pct": round(float(spread_pct), 6),
                    "depth_usd": round(float(depth_usd), 2),
                }

            return {
                "can_trade": True,
                "reason": "ok",
                "spread_pct": round(float(spread_pct), 6),
                "depth_usd": round(float(depth_usd), 2),
            }

        except Exception as e:
            logger.error("liquidity_check_failed", ticker=ticker, error=str(e))
            return {"can_trade": False, "reason": f"error:{e}",
                    "spread_pct": 0, "depth_usd": 0}

    async def _get_orderbook(self, ticker: str) -> Optional[dict]:
        """Fetch orderbook from Bybit."""
        try:
            orderbook = await self.bybit.get_orderbook(ticker, limit=DEPTH_LEVELS)
            if orderbook.get("error"):
                return None
            return orderbook
        except Exception as e:
            logger.warning("orderbook_fetch_failed", ticker=ticker, error=str(e))
        return None


class SlippageEstimator:
    """Estimates slippage for market orders by simulating fill through orderbook."""

    def __init__(self, bybit):
        self.bybit = bybit

    async def estimate_slippage(self, ticker: str, side: str, size_usd: float) -> dict:
        """Estimate slippage for a market order.

        Args:
            ticker: Symbol (e.g. "BTCUSDT")
            side: "BUY" or "SELL"
            size_usd: Order size in USD

        Returns:
            {"slippage_pct": float, "effective_price": float,
             "mid_price": float, "can_execute": bool, "reason": str}
        """
        try:
            orderbook = await self._get_orderbook(ticker)
            if not orderbook:
                return {"slippage_pct": 0, "effective_price": 0, "mid_price": 0,
                        "can_execute": False, "reason": "orderbook_fetch_failed"}

            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])

            if not bids or not asks:
                return {"slippage_pct": 0, "effective_price": 0, "mid_price": 0,
                        "can_execute": False, "reason": "empty_orderbook"}

            best_bid = Decimal(str(bids[0][0]))
            best_ask = Decimal(str(asks[0][0]))
            mid_price = (best_bid + best_ask) / 2

            # Simulate fill through orderbook levels
            books = asks if side.upper() == "BUY" else bids
            remaining_usd = Decimal(str(size_usd))
            total_cost = Decimal("0")
            filled_qty = Decimal("0")

            for level in books:
                level_price = Decimal(str(level[0]))
                level_qty = Decimal(str(level[1]))
                level_value = level_price * level_qty

                if remaining_usd <= 0:
                    break

                if remaining_usd <= level_value:
                    # Partial fill at this level
                    fill_qty = remaining_usd / level_price
                    total_cost += fill_qty * level_price
                    filled_qty += fill_qty
                    remaining_usd = Decimal("0")
                else:
                    # Full fill at this level
                    total_cost += level_value
                    filled_qty += level_qty
                    remaining_usd -= level_value

            if filled_qty == 0:
                return {"slippage_pct": 0, "effective_price": 0, "mid_price": float(mid_price),
                        "can_execute": False, "reason": "insufficient_liquidity"}

            effective_price = total_cost / filled_qty

            # Slippage: how far effective price is from mid
            if side.upper() == "BUY":
                slippage_pct = (effective_price - mid_price) / mid_price
            else:
                slippage_pct = (mid_price - effective_price) / mid_price

            slippage_pct = max(slippage_pct, Decimal("0"))  # negative = price improvement

            return {
                "slippage_pct": round(float(slippage_pct), 6),
                "effective_price": round(float(effective_price), 4),
                "mid_price": round(float(mid_price), 4),
                "can_execute": slippage_pct <= MAX_SLIPPAGE_PCT,
                "reason": "ok" if slippage_pct <= MAX_SLIPPAGE_PCT else f"slippage_too_high:{float(slippage_pct):.4%}",
            }

        except Exception as e:
            logger.error("slippage_estimate_failed", ticker=ticker, error=str(e))
            return {"slippage_pct": 0, "effective_price": 0, "mid_price": 0,
                    "can_execute": False, "reason": f"error:{e}"}

    async def _get_orderbook(self, ticker: str) -> Optional[dict]:
        """Fetch orderbook from Bybit."""
        try:
            orderbook = await self.bybit.get_orderbook(ticker, limit=20)
            if orderbook.get("error"):
                return None
            return orderbook
        except Exception as e:
            logger.warning("orderbook_fetch_failed", ticker=ticker, error=str(e))
        return None
