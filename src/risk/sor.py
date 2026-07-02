"""Karsa Trading System - Smart Order Router (SOR)

Executes approved signals on Bybit testnet.
Post-Only Limit orders at bid/ask for maker rebates.
Timeout + re-pricing logic. Falls back to market order after max retries.

Flow:
  Risk Manager approves → SOR.execute_order() → entry + SL + TP
"""

import asyncio

from src.data.bybit_client import BybitClient
from src.utils.logging import get_logger

logger = get_logger("sor")

LIMIT_TIMEOUT_SEC = 30
MAX_REPRICE_ATTEMPTS = 3


class SmartOrderRouter:
    """Places orders on Bybit with smart routing for best fill."""

    def __init__(self, bybit: BybitClient):
        self.bybit = bybit

    async def execute_order(self, signal: dict, risk_params: dict) -> dict:
        """Execute a risk-approved signal on Bybit."""
        ticker = signal["ticker"]
        direction = signal["direction"]
        qty = risk_params["qty"]
        stop_loss = risk_params["stop_loss"]
        take_profit = risk_params["take_profit"]
        leverage = risk_params.get("leverage", 1)

        if qty <= 0:
            return {"success": False, "error": "Zero quantity"}

        # Set leverage (Bybit returns error 110043 if leverage unchanged — safe to ignore)
        try:
            await asyncio.to_thread(
                self.bybit._http_client.set_leverage,
                category="linear",
                symbol=ticker,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
        except Exception as e:
            err_str = str(e)
            if "110043" in err_str or "not modified" in err_str.lower():
                pass  # Leverage already set — harmless
            else:
                logger.warning("set_leverage_failed", symbol=ticker, error=err_str)

        # Get bid/ask for limit pricing
        orderbook = await self.bybit.get_orderbook(ticker, limit=5)
        if orderbook.get("error"):
            return await self._market_order(ticker, direction, qty, stop_loss, take_profit)

        if direction == "LONG":
            limit_price = orderbook["bids"][0][0] if orderbook.get("bids") else None
            bybit_side = "Buy"
        else:
            limit_price = orderbook["asks"][0][0] if orderbook.get("asks") else None
            bybit_side = "Sell"

        if not limit_price:
            return await self._market_order(ticker, direction, qty, stop_loss, take_profit)

        # Place Post-Only Limit with re-price loop
        for attempt in range(MAX_REPRICE_ATTEMPTS):
            result = await self.bybit.place_order(
                symbol=ticker,
                side=bybit_side,
                qty=qty,
                order_type="Limit",
                price=limit_price,
                time_in_force="PostOnly",
            )

            if result.get("error"):
                logger.warning("limit_order_failed", attempt=attempt, error=result["error"])
                if "too late" in str(result.get("error", "")).lower() or result.get("retCode") == 10001:
                    return await self._market_order(ticker, direction, qty, stop_loss, take_profit)
                await asyncio.sleep(1)
                orderbook = await self.bybit.get_orderbook(ticker, limit=5)
                if direction == "LONG":
                    limit_price = orderbook["bids"][0][0] if orderbook.get("bids") else limit_price
                else:
                    limit_price = orderbook["asks"][0][0] if orderbook.get("asks") else limit_price
                continue

            order_id = result.get("order_id")
            fill_result = await self._wait_for_fill(ticker, order_id, LIMIT_TIMEOUT_SEC)

            if fill_result.get("filled"):
                sl_tp = await self._place_sl_tp(ticker, bybit_side, qty, stop_loss, take_profit)
                logger.info("sor_order_filled", ticker=ticker, side=direction, qty=qty,
                            fill_price=fill_result.get("fill_price"))
                return {
                    "success": True, "order_id": order_id,
                    "fill_price": fill_result.get("fill_price", limit_price),
                    "qty": qty, **sl_tp,
                }

            await self.bybit.cancel_order(ticker, order_id)
            orderbook = await self.bybit.get_orderbook(ticker, limit=5)
            if direction == "LONG":
                limit_price = orderbook["bids"][0][0] if orderbook.get("bids") else limit_price
            else:
                limit_price = orderbook["asks"][0][0] if orderbook.get("asks") else limit_price

        return await self._market_order(ticker, direction, qty, stop_loss, take_profit)

    async def _market_order(self, ticker: str, direction: str, qty: float, stop_loss: float, take_profit: float) -> dict:
        bybit_side = "Buy" if direction == "LONG" else "Sell"
        result = await self.bybit.place_order(symbol=ticker, side=bybit_side, qty=qty, order_type="Market")

        if result.get("error"):
            logger.error("market_order_failed", ticker=ticker, error=result["error"])
            return {"success": False, "error": result["error"]}

        # Fix #5: Get actual fill price from order status (market orders don't return price)
        fill_price = 0
        order_id = result.get("order_id")
        if order_id:
            import asyncio
            await asyncio.sleep(1)  # Brief wait for fill to register
            status = await self.bybit.get_order_status(ticker, order_id)
            fill_price = status.get("avg_price", 0)

        if not fill_price:
            # Fallback: get from position
            positions = await self.bybit.get_positions(ticker)
            for p in positions:
                if p.get("size", 0) > 0:
                    fill_price = p.get("entry_price", 0)
                    break

        sl_tp = await self._place_sl_tp(ticker, bybit_side, qty, stop_loss, take_profit)
        return {"success": True, "order_id": order_id,
                "fill_price": fill_price, "qty": qty, "order_type": "market", **sl_tp}

    async def _place_sl_tp(self, ticker: str, entry_side: str, qty: float, stop_loss: float, take_profit: float) -> dict:
        sl_result = await self.bybit.set_stop_loss(ticker, stop_loss, entry_side)
        tp_result = await self.bybit.set_take_profit(ticker, take_profit, entry_side)
        return {"sl_order_id": sl_result.get("order_id"), "sl_price": stop_loss,
                "tp_order_id": tp_result.get("order_id"), "tp_price": take_profit}

    async def _wait_for_fill(self, ticker: str, order_id: str, timeout: int) -> dict:
        """Poll order status via Bybit order history (not position check)."""
        for _ in range(timeout // 2):
            await asyncio.sleep(2)
            try:
                status = await self.bybit.get_order_status(ticker, order_id)
                if status.get("error"):
                    continue
                order_status = status.get("status", "")
                if order_status == "Filled":
                    return {"filled": True, "fill_price": status.get("avg_price", 0)}
                if order_status in ("Cancelled", "Rejected", "Deactivated"):
                    return {"filled": False, "reason": order_status}
            except Exception:
                continue
        return {"filled": False}

    async def close_position(self, symbol: str, position: dict) -> dict:
        """Close a single position (used for counter-trade flips)."""
        size = position.get("size", 0)
        side = position.get("side", "Buy")
        close_side = "Sell" if side == "Buy" else "Buy"
        try:
            result = await self.bybit.place_order(
                symbol=symbol, side=close_side, qty=size,
                order_type="Market", reduce_only=True,
            )
            if result.get("error"):
                return {"success": False, "error": result["error"]}
            logger.info("counter_trade_position_closed", ticker=symbol, size=size)
            return {"success": True, "order_id": result.get("order_id")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def flatten_all(self) -> dict:
        """Close all open positions. Used by /kill and /sellall."""
        positions = await self.bybit.get_positions()
        closed = []
        for pos in positions:
            ticker = pos["symbol"]
            size = pos["size"]
            side = pos.get("side", "Buy")
            close_side = "Sell" if side == "Buy" else "Buy"
            result = await self.bybit.place_order(
                symbol=ticker, side=close_side, qty=size, order_type="Market", reduce_only=True,
            )
            if result.get("error"):
                logger.error("flatten_failed", ticker=ticker, error=result["error"])
            else:
                closed.append(ticker)
                logger.info("position_closed", ticker=ticker, size=size)
        return {"closed": closed, "count": len(closed)}
