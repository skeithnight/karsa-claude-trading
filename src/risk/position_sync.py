"""Karsa Trading System — Position & Order & Balance Reconciler

Bidirectional reconciliation between Bybit exchange state and local DB.

PositionReconciler — 3 drift types:
- PHANTOM: in DB but not on exchange (stale position)
- MISSING: on exchange but not in DB (new position)
- SIZE_DRIFT: size mismatch between DB and exchange

OrderReconciler — 2 drift types:
- ORPHANED: in DB but not on exchange (stale order)
- UNKNOWN: on exchange but not in DB (untracked order)

BalanceReconciler:
- Balance drift between DB cached and exchange wallet

Flow:
  Scheduler calls reconcile() every 5 min →
  Compare DB positions vs exchange positions →
  Log drifts to crypto_reconciliation_logs →
  Auto-fix: mark phantom as CLOSED, create missing, update drift.
"""

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from typing import Optional

from src.models.database import async_session
from src.models.tables import CryptoPosition, CryptoReconciliationLog
from src.metrics.crypto_metrics import record_reconciliation_ghost
from src.utils.logging import get_logger
from sqlalchemy import select, desc

logger = get_logger("position_sync")

# Size drift tolerance (percentage)
SIZE_DRIFT_TOLERANCE_PCT = Decimal("1.0")  # 1% tolerance for rounding

# Balance drift tolerance (percentage)
BALANCE_DRIFT_TOLERANCE_PCT = Decimal("0.01")  # 0.01% balance drift


class PositionReconciler:
    """Bidirectional position reconciliation."""

    def __init__(self, bybit):
        self.bybit = bybit

    async def reconcile(self) -> list[dict]:
        """Reconcile DB positions with Bybit exchange state.

        Returns list of detected drifts.
        """
        drifts = []

        try:
            # Fetch exchange positions
            exchange_positions = await self._get_exchange_positions()
            # Fetch DB positions
            db_positions = await self._get_db_positions()

            # Index by ticker for comparison
            exchange_by_ticker = {p["symbol"]: p for p in exchange_positions}
            db_by_ticker = {p.ticker: p for p in db_positions}

            # 1. Detect PHANTOM: in DB but not on exchange
            for ticker, db_pos in db_by_ticker.items():
                if ticker not in exchange_by_ticker and db_pos.status == "OPEN":
                    drift = await self._handle_phantom(db_pos, exchange_positions)
                    if drift:
                        drifts.append(drift)

            # 2. Detect MISSING: on exchange but not in DB
            for ticker, exch_pos in exchange_by_ticker.items():
                if ticker not in db_by_ticker:
                    drift = await self._handle_missing(exch_pos)
                    if drift:
                        drifts.append(drift)

            # 3. Detect SIZE_DRIFT: size mismatch
            for ticker in set(exchange_by_ticker.keys()) & set(db_by_ticker.keys()):
                exch_pos = exchange_by_ticker[ticker]
                db_pos = db_by_ticker[ticker]
                if db_pos.status == "OPEN":
                    drift = await self._handle_size_drift(db_pos, exch_pos)
                    if drift:
                        drifts.append(drift)

            # 4. Detect STALE_CLOSED: CLOSED in DB but still active on exchange
            for ticker, exch_pos in exchange_by_ticker.items():
                if ticker in db_by_ticker and db_by_ticker[ticker].status == "CLOSED":
                    drift = await self._handle_stale_closed(exch_pos, db_by_ticker[ticker])
                    if drift:
                        drifts.append(drift)

            if drifts:
                logger.warning("reconciliation_drifts_detected", count=len(drifts))
                for _ in drifts:
                    record_reconciliation_ghost()
                for d in drifts:
                    logger.warning("drift_detail", **d)

        except Exception as e:
            logger.error("reconciliation_failed", error=str(e))

        return drifts

    async def _get_exchange_positions(self) -> list[dict]:
        """Fetch open positions from Bybit."""
        try:
            resp = await asyncio.to_thread(
                self.bybit._http_client.get_positions,
                category="linear",
                settleCoin="USDT",
            )
            if resp.get("retCode") == 0:
                return [p for p in resp.get("result", {}).get("list", [])
                        if float(p.get("size", 0)) > 0]
        except Exception as e:
            logger.error("exchange_positions_fetch_failed", error=str(e))
        return []

    async def _get_db_positions(self) -> list[CryptoPosition]:
        """Fetch all positions from DB."""
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(CryptoPosition).where(
                        CryptoPosition.status.in_(["OPEN", "CLOSED"])
                    )
                )
                return list(result.scalars().all())
        except Exception as e:
            logger.error("db_positions_fetch_failed", error=str(e))
        return []

    async def _handle_phantom(self, db_pos: CryptoPosition, exchange_positions: list[dict]) -> Optional[dict]:
        """Handle phantom position: in DB but not on exchange."""
        try:
            # Position Manager promotion (Phase 3 — single writer)
            from src.architecture.feature_flags import flags
            if flags.is_enabled("position_manager_enabled"):
                from src.architecture.position import PositionManager, SyncFromExchange
                from src.architecture.events import event_bus
                arch_pm = PositionManager(event_bus=event_bus)
                cmd = SyncFromExchange(
                    position_id=f"db:{db_pos.id}",
                    exchange_size=0,
                    exchange_status="CLOSED",
                )
                await arch_pm.sync_from_exchange(cmd)
                logger.info("position_manager_write", ticker=db_pos.ticker, action="phantom_closed")
            else:
                # Step 1: Fetch PnL data from Bybit BEFORE opening DB session.
                # This is the IO-before-session pattern — a 5-15s HTTP call must
                # never hold a DB connection open while it waits.
                exit_price = db_pos.current_price or db_pos.entry_price
                pnl = db_pos.unrealized_pnl or 0
                try:
                    closed_pnl_data = await self.bybit.get_closed_pnl(
                        symbol=db_pos.ticker, limit=5
                    )
                    if closed_pnl_data:
                        for cp in closed_pnl_data:
                            if cp.get("symbol") == db_pos.ticker:
                                exit_price = Decimal(str(cp["exit_price"])) if cp["exit_price"] else exit_price
                                pnl = Decimal(str(cp["closed_pnl"])) if cp["closed_pnl"] else pnl
                                logger.info("phantom_pnl_from_exchange",
                                            ticker=db_pos.ticker,
                                            exit_price=str(exit_price),
                                            pnl=str(pnl))
                                break
                except Exception as e:
                    logger.warning("phantom_closed_pnl_fetch_failed",
                                   ticker=db_pos.ticker, error=str(e),
                                   fallback="using_unrealized_pnl")

                # Step 2: Write to DB — connection held only during fast local ops
                async with async_session() as session:
                    pos = await session.get(CryptoPosition, db_pos.id)
                    if pos:
                        pos.status = "CLOSED"
                        pos.last_synced_at = datetime.utcnow()

                        try:
                            from src.models.tables import ClosedPaperTrade

                            pnl_pct = float((exit_price - pos.entry_price) / pos.entry_price * 100) if pos.entry_price else 0
                            if pos.side == "Sell":
                                pnl_pct = -pnl_pct

                            session.add(ClosedPaperTrade(
                                ticker=pos.ticker,
                                market="CRYPTO",
                                side="LONG" if pos.side == "Buy" else "SHORT",
                                quantity=pos.size,
                                entry_price=pos.entry_price,
                                exit_price=exit_price,
                                realized_pnl=pnl,
                                realized_pnl_pct=pnl_pct,
                                entry_date=pos.opened_at,
                                exit_date=datetime.utcnow(),
                                exit_reason="phantom_sync"
                            ))
                            from src.metrics.crypto_metrics import record_trade_close
                            record_trade_close(
                                float(pnl),
                                "win" if pnl > 0 else "loss",
                                ticker=pos.ticker,
                                exit_price=float(exit_price),
                                closed_time=datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
                            )
                        except Exception as e:
                            logger.error("closed_paper_trade_insert_failed", error=str(e))

                        session.add(CryptoReconciliationLog(
                            position_id=db_pos.id,
                            drift_type="PHANTOM",
                            exchange_state={"exists": False},
                            db_state={
                                "ticker": db_pos.ticker,
                                "side": db_pos.side,
                                "size": str(db_pos.size),
                                "status": db_pos.status,
                            },
                            resolution="marked_closed",
                        ))
                        await session.commit()

            logger.info("phantom_position_closed", ticker=db_pos.ticker, position_id=db_pos.id)
            return {
                "drift_type": "PHANTOM",
                "ticker": db_pos.ticker,
                "position_id": db_pos.id,
                "resolution": "marked_closed",
            }
        except Exception as e:
            logger.error("phantom_handle_failed", ticker=db_pos.ticker, error=str(e))
        return None

    async def _handle_missing(self, exch_pos: dict) -> Optional[dict]:
        """Handle missing position: on exchange but not in DB.

        Pulls SL/TP from Bybit position data so trailing stop and
        profit lock can manage the position immediately.
        """
        try:
            symbol = exch_pos.get("symbol", "")
            side = exch_pos.get("side", "")
            size = Decimal(str(exch_pos.get("size", 0)))
            entry_price = Decimal(str(exch_pos.get("avgPrice", 0)))
            leverage = int(exch_pos.get("leverage", 1))
            mark_price = Decimal(str(exch_pos.get("markPrice", 0)))

            # Pull SL/TP from Bybit (already in position response)
            sl_raw = exch_pos.get("stopLoss", "")
            tp_raw = exch_pos.get("takeProfit", "")
            sl_price = Decimal(str(sl_raw)) if sl_raw and float(sl_raw) > 0 else None
            tp_price = Decimal(str(tp_raw)) if tp_raw and float(tp_raw) > 0 else None

            async with async_session() as session:
                # UPSERT: insert or update if position already exists
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                stmt = pg_insert(CryptoPosition).values(
                    ticker=symbol,
                    side=side,
                    size=size,
                    entry_price=entry_price,
                    current_price=mark_price,
                    leverage=leverage,
                    margin_mode=exch_pos.get("marginMode", "isolated"),
                    liquidation_price=Decimal(str(exch_pos.get("liqPrice", 0))) if exch_pos.get("liqPrice") else None,
                    unrealized_pnl=Decimal(str(exch_pos.get("unrealisedPnl", 0))),
                    stop_loss=sl_price,
                    take_profit=tp_price,
                    trailing_stop_price=sl_price,
                    highest_price=max(entry_price, mark_price),
                    status="OPEN",
                    opened_at=datetime.utcnow(),
                    last_synced_at=datetime.utcnow(),
                ).on_conflict_do_update(
                    index_elements=["ticker", "side"],
                    index_where=CryptoPosition.status == "OPEN",
                    set_={
                        "size": size,
                        "current_price": mark_price,
                        "unrealized_pnl": Decimal(str(exch_pos.get("unrealisedPnl", 0))),
                        "stop_loss": sl_price,
                        "take_profit": tp_price,
                        "last_synced_at": datetime.utcnow(),
                    },
                )
                await session.execute(stmt)

                session.add(CryptoReconciliationLog(
                    drift_type="MISSING",
                    exchange_state={
                        "symbol": symbol,
                        "side": side,
                        "size": str(size),
                        "entry_price": str(entry_price),
                    },
                    db_state={"exists": False},
                    resolution="created_from_exchange",
                ))
                await session.commit()

            logger.info("missing_position_created", ticker=symbol, side=side, size=str(size))
            return {
                "drift_type": "MISSING",
                "ticker": symbol,
                "resolution": "created_from_exchange",
            }
        except Exception as e:
            logger.error("missing_handle_failed", error=str(e))
        return None

    async def _handle_stale_closed(self, exch_pos: dict, db_pos: CryptoPosition) -> Optional[dict]:
        """Handle stale closed: position CLOSED in DB but still active on exchange.

        Creates a fresh OPEN record with current exchange state so trailing
        stop, profit lock, and perf gate can manage it.
        """
        try:
            symbol = exch_pos.get("symbol", "")
            side = exch_pos.get("side", "")
            size = Decimal(str(exch_pos.get("size", 0)))
            entry_price = Decimal(str(exch_pos.get("avgPrice", 0)))
            leverage = int(exch_pos.get("leverage", 1))
            mark_price = Decimal(str(exch_pos.get("markPrice", 0)))

            sl_raw = exch_pos.get("stopLoss", "")
            tp_raw = exch_pos.get("takeProfit", "")
            sl_price = Decimal(str(sl_raw)) if sl_raw and float(sl_raw) > 0 else None
            tp_price = Decimal(str(tp_raw)) if tp_raw and float(tp_raw) > 0 else None

            async with async_session() as session:
                # UPSERT: insert or update if position already exists
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                stmt = pg_insert(CryptoPosition).values(
                    ticker=symbol,
                    side=side,
                    size=size,
                    entry_price=entry_price,
                    current_price=mark_price,
                    leverage=leverage,
                    margin_mode=exch_pos.get("marginMode", "isolated"),
                    unrealized_pnl=Decimal(str(exch_pos.get("unrealisedPnl", 0))),
                    stop_loss=sl_price,
                    take_profit=tp_price,
                    trailing_stop_price=sl_price,
                    highest_price=max(entry_price, mark_price),
                    status="OPEN",
                    opened_at=datetime.utcnow(),
                    last_synced_at=datetime.utcnow(),
                ).on_conflict_do_update(
                    index_elements=["ticker", "side"],
                    index_where=CryptoPosition.status == "OPEN",
                    set_={
                        "size": size,
                        "current_price": mark_price,
                        "unrealized_pnl": Decimal(str(exch_pos.get("unrealisedPnl", 0))),
                        "stop_loss": sl_price,
                        "take_profit": tp_price,
                        "last_synced_at": datetime.utcnow(),
                    },
                )
                await session.execute(stmt)

                session.add(CryptoReconciliationLog(
                    position_id=db_pos.id,
                    drift_type="STALE_CLOSED",
                    exchange_state={"symbol": symbol, "side": side, "size": str(size)},
                    db_state={"status": db_pos.status, "id": db_pos.id},
                    resolution="reopened_from_exchange",
                ))
                await session.commit()

            logger.warning("stale_closed_reopened", ticker=symbol, old_id=db_pos.id, side=side, size=str(size))
            return {
                "drift_type": "STALE_CLOSED",
                "ticker": symbol,
                "old_id": db_pos.id,
                "resolution": "reopened_from_exchange",
            }
        except Exception as e:
            logger.error("stale_closed_handle_failed", error=str(e))
        return None

    async def _handle_size_drift(self, db_pos: CryptoPosition, exch_pos: dict) -> Optional[dict]:
        """Handle size drift: size mismatch between DB and exchange."""
        try:
            db_size = Decimal(str(db_pos.size))
            exch_size = Decimal(str(exch_pos.get("size", 0)))

            if db_size == 0:
                return None

            drift_pct = abs(db_size - exch_size) / db_size * 100
            if drift_pct <= SIZE_DRIFT_TOLERANCE_PCT:
                return None  # within tolerance

            # Update DB to match exchange
            async with async_session() as session:
                pos = await session.get(CryptoPosition, db_pos.id)
                if pos:
                    pos.size = exch_size
                    pos.current_price = Decimal(str(exch_pos.get("markPrice", 0)))
                    pos.unrealized_pnl = Decimal(str(exch_pos.get("unrealisedPnl", 0)))
                    pos.last_synced_at = datetime.utcnow()

                    session.add(CryptoReconciliationLog(
                        position_id=db_pos.id,
                        drift_type="SIZE_DRIFT",
                        exchange_state={
                            "symbol": exch_pos.get("symbol"),
                            "size": str(exch_size),
                        },
                        db_state={
                            "ticker": db_pos.ticker,
                            "size": str(db_size),
                        },
                        resolution=f"updated_to_exchange_{drift_pct:.1f}pct",
                    ))
                    await session.commit()

            logger.warning("size_drift_corrected",
                           ticker=db_pos.ticker,
                           db_size=str(db_size),
                           exchange_size=str(exch_size),
                           drift_pct=round(float(drift_pct), 2))
            return {
                "drift_type": "SIZE_DRIFT",
                "ticker": db_pos.ticker,
                "db_size": str(db_size),
                "exchange_size": str(exch_size),
                "drift_pct": round(float(drift_pct), 2),
                "resolution": "updated_to_exchange",
            }
        except Exception as e:
            logger.error("size_drift_handle_failed", ticker=db_pos.ticker, error=str(e))
        return None


class OrderReconciler:
    """Detects orphaned and unknown orders between DB and exchange."""

    def __init__(self, bybit):
        self.bybit = bybit

    async def reconcile_orders(self) -> list[dict]:
        """Reconcile DB orders with Bybit exchange orders.

        Returns list of detected drifts (ORPHANED / UNKNOWN).
        """
        drifts = []
        try:
            # Fetch exchange open orders
            exchange_orders = await self._get_exchange_orders()
            # Fetch DB recent orders (open/pending status)
            db_orders = await self._get_db_orders()

            exchange_ids = {o["orderId"] for o in exchange_orders}
            db_ids = {o.order_id for o in db_orders if o.order_id}

            # ORPHANED: in DB but not on exchange
            for db_order in db_orders:
                if db_order.order_id and db_order.order_id not in exchange_ids:
                    drift = await self._handle_orphaned(db_order)
                    if drift:
                        drifts.append(drift)

            # UNKNOWN: on exchange but not in DB
            for exch_order in exchange_orders:
                if exch_order["orderId"] not in db_ids:
                    drift = await self._handle_unknown(exch_order)
                    if drift:
                        drifts.append(drift)

            if drifts:
                logger.warning("order_reconciliation_drifts", count=len(drifts))

        except Exception as e:
            logger.error("order_reconciliation_failed", error=str(e))

        return drifts

    async def _get_exchange_orders(self) -> list[dict]:
        """Fetch open orders from Bybit."""
        try:
            resp = await asyncio.to_thread(
                self.bybit._http_client.get_open_orders,
                category="linear",
            )
            if resp.get("retCode") == 0:
                return resp.get("result", {}).get("list", [])
        except Exception as e:
            logger.error("exchange_orders_fetch_failed", error=str(e))
        return []

    async def _get_db_orders(self) -> list:
        """Fetch recent orders from DB (positions with pending status or order_ids)."""
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(CryptoPosition)
                    .where(CryptoPosition.status.in_(["PENDING", "OPEN"]))
                    .order_by(desc(CryptoPosition.created_at))
                    .limit(100)
                )
                return list(result.scalars().all())
        except Exception as e:
            logger.error("db_orders_fetch_failed", error=str(e))
        return []

    async def _handle_orphaned(self, db_order) -> Optional[dict]:
        """Handle orphaned order: in DB but not on exchange."""
        try:
            async with async_session() as session:
                pos = await session.get(CryptoPosition, db_order.id)
                if pos and pos.status == "PENDING":
                    pos.status = "CLOSED"
                    session.add(CryptoReconciliationLog(
                        position_id=db_order.id,
                        drift_type="PHANTOM",
                        exchange_state={"exists": False, "order_id": db_order.order_id},
                        db_state={"ticker": db_order.ticker, "status": "PENDING"},
                        resolution="orphaned_order_closed",
                    ))
                    await session.commit()

            logger.info("orphaned_order_closed",
                        ticker=db_order.ticker,
                        order_id=db_order.order_id)
            return {
                "drift_type": "ORPHANED",
                "ticker": db_order.ticker,
                "order_id": db_order.order_id,
                "resolution": "closed",
            }
        except Exception as e:
            logger.error("orphaned_handle_failed", error=str(e))
        return None

    async def _handle_unknown(self, exch_order: dict) -> Optional[dict]:
        """Handle unknown order: on exchange but not in DB."""
        try:
            symbol = exch_order.get("symbol", "")
            order_id = exch_order.get("orderId", "")
            side = exch_order.get("side", "")
            qty = exch_order.get("qty", "0")
            price = exch_order.get("price", "0")

            logger.warning("unknown_exchange_order",
                           symbol=symbol,
                           order_id=order_id,
                           side=side,
                           qty=qty)

            # Log but don't auto-cancel (could be manual order)
            async with async_session() as session:
                session.add(CryptoReconciliationLog(
                    drift_type="UNKNOWN",
                    exchange_state={
                        "symbol": symbol,
                        "order_id": order_id,
                        "side": side,
                        "qty": qty,
                        "price": price,
                    },
                    db_state={"exists": False},
                    resolution="logged_unknown",
                ))
                await session.commit()

            return {
                "drift_type": "UNKNOWN",
                "ticker": symbol,
                "order_id": order_id,
                "resolution": "logged",
            }
        except Exception as e:
            logger.error("unknown_order_handle_failed", error=str(e))
        return None


class BalanceReconciler:
    """Detects balance drift between DB cached state and Bybit wallet."""

    def __init__(self, bybit):
        self.bybit = bybit

    async def reconcile_balances(self) -> list[dict]:
        """Reconcile DB balance with Bybit wallet balance.

        Returns list of drifts detected.
        """
        drifts = []
        try:
            # Fetch exchange wallet balance
            exchange_balance = await self._get_exchange_balance()
            if not exchange_balance:
                return []

            # Fetch DB cached balance (from most recent CryptoPosition or manual cache)
            db_balance = await self._get_db_balance()

            for currency, exch_amt in exchange_balance.items():
                db_amt = db_balance.get(currency, Decimal("0"))
                if db_amt == 0 and exch_amt == 0:
                    continue

                max_amt = max(abs(db_amt), abs(exch_amt), Decimal("1"))
                drift_pct = abs(db_amt - exch_amt) / max_amt * 100

                if drift_pct > BALANCE_DRIFT_TOLERANCE_PCT:
                    drift = {
                        "drift_type": "BALANCE",
                        "currency": currency,
                        "db_balance": str(db_amt),
                        "exchange_balance": str(exch_amt),
                        "drift_pct": round(float(drift_pct), 4),
                        "resolution": "exchange_trusted",
                    }
                    drifts.append(drift)

                    # Log to reconciliation table
                    await self._log_balance_drift(drift)

                    logger.warning("balance_drift_detected",
                                   currency=currency,
                                   db=str(db_amt),
                                   exchange=str(exch_amt),
                                   drift_pct=round(float(drift_pct), 4))

            if not drifts:
                logger.debug("balance_reconciled_ok")

        except Exception as e:
            logger.error("balance_reconciliation_failed", error=str(e))

        return drifts

    async def _get_exchange_balance(self) -> dict[str, Decimal]:
        """Fetch wallet balance from Bybit."""
        try:
            resp = await asyncio.to_thread(
                self.bybit._http_client.get_wallet_balance,
                accountType="UNIFIED",
            )
            if resp.get("retCode") != 0:
                return {}

            balances = {}
            for coin in resp.get("result", {}).get("list", [{}])[0].get("coin", []):
                currency = coin.get("coin", "")
                equity = Decimal(str(coin.get("equity", "0") or "0"))
                if equity > 0:
                    balances[currency] = equity
            return balances
        except Exception as e:
            logger.error("exchange_balance_fetch_failed", error=str(e))
        return {}

    async def _get_db_balance(self) -> dict[str, Decimal]:
        """Fetch cached balance from DB.

        Uses the most recent unrealized_pnl + entry_price as proxy
        if no explicit balance cache exists.
        """
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(CryptoPosition)
                    .where(CryptoPosition.status == "OPEN")
                )
                positions = result.scalars().all()

                # Aggregate by currency (USDT for perps)
                total = Decimal("0")
                for pos in positions:
                    total += Decimal(str(pos.unrealized_pnl or 0))

                return {"USDT": total} if total else {}
        except Exception as e:
            logger.error("db_balance_fetch_failed", error=str(e))
        return {}

    async def _log_balance_drift(self, drift: dict) -> None:
        """Log balance drift to crypto_reconciliation_logs."""
        try:
            async with async_session() as session:
                session.add(CryptoReconciliationLog(
                    drift_type="PHANTOM",  # closest match in CHECK constraint
                    exchange_state={
                        "currency": drift["currency"],
                        "balance": drift["exchange_balance"],
                    },
                    db_state={
                        "currency": drift["currency"],
                        "balance": drift["db_balance"],
                    },
                    resolution=f"balance_drift_{drift['drift_pct']}pct",
                ))
                await session.commit()
        except Exception as e:
            logger.error("balance_drift_log_failed", error=str(e))
