"""Karsa Trading System - Smart Order Router (SOR)

Executes approved signals on Bybit testnet.
Post-Only Limit orders at bid/ask for maker rebates.
Timeout + re-pricing logic. Falls back to market order after max retries.

Flow:
  Risk Manager approves → SOR.execute_order() → entry + SL + TP
"""

import asyncio
import json
import time
import uuid

from src.config import settings
from src.data.bybit_client import BybitClient
from src.metrics.crypto_metrics import record_order_maker, record_order_taker, update_cumulative_slippage
from src.metrics.crypto_metrics import (
    record_order_fill, record_slippage, record_fill_latency,
    record_limit_fallback, record_order_rejected,
)
from src.utils.logging import get_logger

logger = get_logger("sor")

LIMIT_TIMEOUT_SEC = 30
MAX_REPRICE_ATTEMPTS = 3


class SmartOrderRouter:
    """Places orders on Bybit with smart routing for best fill."""

    def __init__(self, bybit: BybitClient, oms=None):
        self.bybit = bybit
        self.oms = oms

    async def execute_order(self, signal: dict, risk_params: dict) -> dict:
        """Execute a risk-approved signal on Bybit."""
        ticker = signal["ticker"]
        direction = signal["direction"]
        qty = risk_params["qty"]
        stop_loss = risk_params.get("stop_loss")
        take_profit = risk_params.get("take_profit")
        leverage = risk_params.get("leverage", 1)
        reduce_only = risk_params.get("reduce_only", False)

        if qty <= 0:
            return {"success": False, "error": "Zero quantity"}
            
        if settings.TRADING_MODE == "paper":
            logger.info("sor_paper_trade_mock", ticker=ticker, direction=direction, qty=qty)
            # Mock fill at current price
            ticker_data = await self.bybit.get_ticker(ticker)
            fill_price = ticker_data.get("price") if ticker_data else signal.get("entry_price", 0)
            return {
                "success": True, 
                "order_id": f"paper_{int(time.time()*1000)}",
                "fill_price": fill_price,
                "qty": qty,
                "stop_loss_id": "paper_sl",
                "take_profit_id": "paper_tp"
            }

        _start_time = time.time()

        # Round qty to valid lot size
        qty = await self._round_qty(ticker, qty)
        if qty <= 0:
            return {"success": False, "error": "Qty rounds to zero"}

        # Pre-check: order value must meet Bybit's $5 minimum
        try:
            ticker_data = await self.bybit.get_ticker(ticker)
            price = float(ticker_data.get("price", 0))
            if price > 0 and qty * price < 4.50:
                return {"success": False, "error": f"Order value ${qty * price:.2f} below Bybit $5 minimum"}
        except Exception:
            pass

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
            return await self._market_order(ticker, direction, qty, stop_loss, take_profit, reduce_only)

        if direction == "LONG":
            limit_price = orderbook["bids"][0][0] if orderbook.get("bids") else None
            bybit_side = "Buy"
        else:
            limit_price = orderbook["asks"][0][0] if orderbook.get("asks") else None
            bybit_side = "Sell"

        if not limit_price:
            return await self._market_order(ticker, direction, qty, stop_loss, take_profit, reduce_only)

        # Place Post-Only Limit with re-price loop
        order_link_id = f"karsa-{uuid.uuid4().hex[:16]}"
        for attempt in range(MAX_REPRICE_ATTEMPTS):
            result = await self.bybit.place_order(
                symbol=ticker,
                side=bybit_side,
                qty=qty,
                order_type="Limit",
                price=limit_price,
                time_in_force="PostOnly",
                reduce_only=reduce_only,
                order_link_id=order_link_id,
            )

            if result.get("error"):
                logger.warning("limit_order_failed", attempt=attempt, error=result["error"])
                if "too late" in str(result.get("error", "")).lower() or result.get("retCode") == 10001:
                    return await self._market_order(ticker, direction, qty, stop_loss, take_profit, reduce_only)
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
                sl_tp = {}
                if stop_loss and take_profit:
                    sl_tp = await self._place_sl_tp(ticker, bybit_side, qty, stop_loss, take_profit)
                logger.info("sor_order_filled", ticker=ticker, side=direction, qty=qty,
                            fill_price=fill_result.get("fill_price"))
                record_order_fill(ticker, "limit", direction)
                record_fill_latency(time.time() - _start_time)

                # Wire slippage tracking to Prometheus
                actual_fill = float(fill_result.get("fill_price", limit_price))
                if limit_price and limit_price > 0:
                    slippage_bps = abs(actual_fill - limit_price) / limit_price * 10000
                    record_slippage(ticker, direction, slippage_bps)

                # Calculate trading fee (Bybit taker fee: 0.055%)
                TAKER_FEE_PCT = 0.055
                fee_usd = actual_fill * qty * TAKER_FEE_PCT / 100 if actual_fill and qty else 0

                await self._track_order(order_id, ticker, direction, qty,
                                        fill_result.get("fill_price", limit_price), "Filled")
                return {
                    "success": True, "order_id": order_id,
                    "fill_price": fill_result.get("fill_price", limit_price),
                    "qty": qty, "fee_usd": round(fee_usd, 6), **sl_tp,
                }

            await self.bybit.cancel_order(ticker, order_id)
            orderbook = await self.bybit.get_orderbook(ticker, limit=5)
            if direction == "LONG":
                limit_price = orderbook["bids"][0][0] if orderbook.get("bids") else limit_price
            else:
                limit_price = orderbook["asks"][0][0] if orderbook.get("asks") else limit_price

        record_limit_fallback(ticker, "max_reprice")
        return await self._market_order(ticker, direction, qty, stop_loss, take_profit, reduce_only)

    async def _market_order(self, ticker: str, direction: str, qty: float, stop_loss: float, take_profit: float, reduce_only: bool = False) -> dict:
        bybit_side = "Buy" if direction == "LONG" else "Sell"
        order_link_id = f"karsa-m-{uuid.uuid4().hex[:16]}"

        # Retry market order up to 3 times
        result = None
        for attempt in range(3):
            result = await self.bybit.place_order(
                symbol=ticker, side=bybit_side, qty=qty, order_type="Market",
                reduce_only=reduce_only, order_link_id=order_link_id,
            )
            if not result.get("error"):
                break
            logger.warning("market_order_retry", ticker=ticker, attempt=attempt + 1,
                           error=result.get("error"))
            await asyncio.sleep(1 * (attempt + 1))

        if result.get("error"):
            logger.error("market_order_failed", ticker=ticker, error=result["error"])
            record_order_rejected(ticker, str(result.get("retCode", "unknown")))
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

        sl_tp = {}
        if stop_loss and take_profit:
            sl_tp = await self._place_sl_tp(ticker, bybit_side, qty, stop_loss, take_profit)

        # Calculate trading fee (Bybit taker fee: 0.055%)
        TAKER_FEE_PCT = 0.055
        fee_usd = float(fill_price) * qty * TAKER_FEE_PCT / 100 if fill_price and qty else 0

        record_order_fill(ticker, "market", direction)
        await self._track_order(order_id, ticker, direction, qty, fill_price, "Filled")
        return {"success": True, "order_id": order_id,
                "fill_price": fill_price, "qty": qty, "fee_usd": round(fee_usd, 6),
                "order_type": "market", **sl_tp}

    async def _place_sl_tp(self, ticker: str, entry_side: str, qty: float, stop_loss: float, take_profit: float) -> dict:
        sl_result, tp_result = None, None
        sl_ok, tp_ok = False, False

        # Retry SL placement (3 attempts with backoff)
        for attempt in range(3):
            sl_result = await self.bybit.set_stop_loss(ticker, stop_loss, entry_side)
            if sl_result and not sl_result.get("error"):
                sl_ok = True
                break
            logger.warning("sl_placement_retry", ticker=ticker, attempt=attempt + 1,
                           error=sl_result.get("error") if sl_result else "no_response")
            await asyncio.sleep(1 * (attempt + 1))

        # Retry TP placement (3 attempts with backoff)
        for attempt in range(3):
            tp_result = await self.bybit.set_take_profit(ticker, take_profit, entry_side)
            if tp_result and not tp_result.get("error"):
                tp_ok = True
                break
            logger.warning("tp_placement_retry", ticker=ticker, attempt=attempt + 1,
                           error=tp_result.get("error") if tp_result else "no_response")
            await asyncio.sleep(1 * (attempt + 1))

        # Alert on final failure — position left unprotected
        if not sl_ok:
            logger.error("sl_placement_failed", ticker=ticker, stop_loss=stop_loss,
                         error=sl_result.get("error") if sl_result else "no_response")
            try:
                if hasattr(self, '_redis') and self._redis:
                    await self._redis.publish("karsa:alerts:critical", json.dumps({
                        "type": "sl_placement_failed",
                        "ticker": ticker,
                        "stop_loss": stop_loss,
                        "error": sl_result.get("error") if sl_result else "no_response",
                    }))
            except Exception:
                pass

        if not tp_ok:
            logger.error("tp_placement_failed", ticker=ticker, take_profit=take_profit,
                         error=tp_result.get("error") if tp_result else "no_response")

        return {"sl_order_id": (sl_result or {}).get("order_id"), "sl_price": stop_loss,
                "tp_order_id": (tp_result or {}).get("order_id"), "tp_price": take_profit,
                "sl_ok": sl_ok, "tp_ok": tp_ok}

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

    async def _track_order(self, order_id: str, ticker: str, direction: str,
                            qty: float, fill_price: float, status: str) -> None:
        """Track order in OMS if available."""
        if not self.oms or not order_id:
            return
        try:
            await self.oms.track_order(
                order_id=order_id,
                ticker=ticker,
                side="Buy" if direction == "LONG" else "Sell",
                quantity=qty,
                order_type="market",
                avg_fill_price=fill_price,
            )
            if status == "Filled":
                await self.oms.update_status(order_id, "FILLED",
                                             filled_qty=qty, avg_price=fill_price)
        except Exception as e:
            logger.warning("oms_track_failed", order_id=order_id, error=str(e))

    async def _round_qty(self, ticker: str, qty: float) -> float:
        """Round qty to valid Bybit lot size step."""
        try:
            info = await asyncio.to_thread(
                self.bybit._http_client.get_instruments_info,
                category="linear",
                symbol=ticker,
            )
            lot = info["result"]["list"][0]["lotSizeFilter"]
            step = float(lot["qtyStep"])
            min_qty = float(lot["minOrderQty"])
            rounded = int(qty / step) * step  # floor to step
            # Format to step precision
            precision = max(0, len(str(step).rstrip('0').split('.')[-1])) if '.' in str(step) else 0
            rounded = round(rounded, precision)
            return rounded if rounded >= min_qty else 0.0
        except Exception as e:
            logger.warning("round_qty_failed", ticker=ticker, error=str(e))
            return qty  # fallback: pass through

    async def verify_close_filled(self, symbol: str, order_id: str, timeout: int = 30) -> dict:
        """Verify a close order actually filled before recording PnL.

        Polls Bybit order status until FILLED/CANCELLED/REJECTED.
        Returns {"filled": True, "fill_price": float, "fill_qty": float} or {"filled": False, "reason": str}.
        """
        for _ in range(timeout // 2):
            await asyncio.sleep(2)
            try:
                status = await self.bybit.get_order_status(symbol, order_id)
                if status.get("error"):
                    continue
                order_status = status.get("status", "")
                if order_status == "Filled":
                    return {
                        "filled": True,
                        "fill_price": status.get("avg_price", 0),
                        "fill_qty": status.get("filled_qty", 0),
                    }
                if order_status in ("Cancelled", "Rejected", "Deactivated"):
                    return {"filled": False, "reason": order_status}
            except Exception:
                continue
        return {"filled": False, "reason": "timeout"}

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

    # --- Iceberg Orders (Phase 6A) ---

    ICEBERG_THRESHOLD_USD = 10000  # Split orders above $10k
    ICEBERG_SLICES = 4            # Number of slices

    async def execute_iceberg_order(self, ticker: str, direction: str, qty: float,
                                     stop_loss: float, take_profit: float,
                                     leverage: int = 1) -> dict:
        """Execute large order as iceberg (split into multiple slices).

        Splits total qty into ICEBERG_SLICES limit orders at incrementally worse prices.
        Each slice waits for fill before placing the next.
        """
        slice_qty = qty / self.ICEBERG_SLICES
        slice_qty = await self._round_qty(ticker, slice_qty)
        if slice_qty <= 0:
            return await self.execute_order(
                {"ticker": ticker, "direction": direction, "entry_price": 0},
                {"qty": qty, "stop_loss": stop_loss, "take_profit": take_profit, "leverage": leverage},
            )

        filled_qty = 0
        avg_price = 0
        bybit_side = "Buy" if direction == "LONG" else "Sell"

        orderbook = await self.bybit.get_orderbook(ticker, limit=10)
        if orderbook.get("error"):
            return await self._market_order(ticker, direction, qty, stop_loss, take_profit)

        base_price = float(orderbook["bids"][0][0] if direction == "LONG" else orderbook["asks"][0][0])
        tick_size = 0.01  # simplified; in prod fetch from instruments

        for i in range(self.ICEBERG_SLICES):
            # Incrementally worse price for each slice
            offset = tick_size * (i + 1)
            if direction == "LONG":
                slice_price = base_price - offset
            else:
                slice_price = base_price + offset

            result = await self.bybit.place_order(
                symbol=ticker, side=bybit_side, qty=slice_qty,
                order_type="Limit", price=slice_price, time_in_force="PostOnly",
            )
            if result.get("error"):
                logger.warning("iceberg_slice_failed", slice=i, error=result["error"])
                continue

            order_id = result.get("order_id")
            fill = await self._wait_for_fill(ticker, order_id, LIMIT_TIMEOUT_SEC)
            if fill.get("filled"):
                fill_price = float(fill["fill_price"])
                avg_price = (avg_price * filled_qty + fill_price * slice_qty) / (filled_qty + slice_qty) if filled_qty > 0 else fill_price
                filled_qty += slice_qty
            else:
                await self.bybit.cancel_order(ticker, order_id)

        if filled_qty <= 0:
            return {"success": False, "error": "No iceberg slices filled"}

        # Place SL/TP on filled quantity
        sl_tp = {}
        if stop_loss and take_profit:
            sl_tp = await self._place_sl_tp(ticker, bybit_side, filled_qty, stop_loss, take_profit)

        TAKER_FEE_PCT = 0.055
        fee_usd = avg_price * filled_qty * TAKER_FEE_PCT / 100

        return {
            "success": True, "fill_price": round(avg_price, 4),
            "qty": filled_qty, "fee_usd": round(fee_usd, 6),
            "iceberg_slices": self.ICEBERG_SLICES, **sl_tp,
        }

    # --- TWAP Execution (Phase 6B) ---

    TWAP_THRESHOLD_USD = 5000   # Use TWAP for orders above $5k
    TWAP_DURATION_SEC = 1800    # Spread over 30 minutes
    TWAP_SLICES = 6             # 6 slices over 30 min = 5 min each

    async def execute_twap_order(self, ticker: str, direction: str, qty: float,
                                   stop_loss: float, take_profit: float,
                                   leverage: int = 1) -> dict:
        """Execute order using TWAP (Time-Weighted Average Price).

        Splits total qty into TWAP_SLICES equal parts over TWAP_DURATION_SEC.
        """
        slice_qty = qty / self.TWAP_SLICES
        slice_qty = await self._round_qty(ticker, slice_qty)
        if slice_qty <= 0:
            return await self.execute_order(
                {"ticker": ticker, "direction": direction, "entry_price": 0},
                {"qty": qty, "stop_loss": stop_loss, "take_profit": take_profit, "leverage": leverage},
            )

        filled_qty = 0
        avg_price = 0
        bybit_side = "Buy" if direction == "LONG" else "Sell"
        interval = self.TWAP_DURATION_SEC // self.TWAP_SLICES

        for i in range(self.TWAP_SLICES):
            if i > 0:
                await asyncio.sleep(interval)

            # Use current market price for each slice
            result = await self.bybit.place_order(
                symbol=ticker, side=bybit_side, qty=slice_qty,
                order_type="Market", reduce_only=False,
            )
            if result.get("error"):
                logger.warning("twap_slice_failed", slice=i, error=result["error"])
                continue

            order_id = result.get("order_id")
            await asyncio.sleep(1)
            status = await self.bybit.get_order_status(ticker, order_id)
            fill_price = float(status.get("avg_price", 0))
            if fill_price > 0:
                avg_price = (avg_price * filled_qty + fill_price * slice_qty) / (filled_qty + slice_qty) if filled_qty > 0 else fill_price
                filled_qty += slice_qty

        if filled_qty <= 0:
            return {"success": False, "error": "No TWAP slices filled"}

        sl_tp = {}
        if stop_loss and take_profit:
            sl_tp = await self._place_sl_tp(ticker, bybit_side, filled_qty, stop_loss, take_profit)

        TAKER_FEE_PCT = 0.055
        fee_usd = avg_price * filled_qty * TAKER_FEE_PCT / 100

        return {
            "success": True, "fill_price": round(avg_price, 4),
            "qty": filled_qty, "fee_usd": round(fee_usd, 6),
            "twap_slices": self.TWAP_SLICES, **sl_tp,
        }
