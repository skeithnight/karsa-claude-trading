"""Karsa Trading System — Autonomous Session Manager (ASM)

Supervisory control layer for autonomous crypto trading.
All state lives in Redis — survives container crashes.
Position sizing uses availableCash (not equity with floating PnL).

Flow:
  User clicks Start → ASM stores config in Redis → spawns _run_loop() →
  Loop: regime gate → scan → risk gate → SOR execute → progress update →
  User clicks Stop → sets Redis flag → loop exits → MTM report sent.
"""

import asyncio
import json
import time
from datetime import datetime, timezone

from src.utils.logging import get_logger

logger = get_logger("autonomous_session")

# Redis keys
REDIS_ACTIVE = "karsa:auto:state:active"
REDIS_CONFIG = "karsa:auto:config"
REDIS_START_TIME = "karsa:auto:start_time"
REDIS_START_EQUITY = "karsa:auto:start_equity"
REDIS_PROGRESS_TS = "karsa:auto:last_progress_ts"

# Default config
DEFAULT_RISK_PCT = 1.5
DEFAULT_MAX_POS = 3
DEFAULT_INTERVAL_MIN = 15
PROGRESS_COOLDOWN_SEC = 3600  # 1 hour


class AutonomousSessionManager:
    """Autonomous trading loop — state machine backed by Redis."""

    def __init__(self, orchestrator, redis, bybit_client):
        self.orchestrator = orchestrator
        self.redis = redis
        self.bybit = bybit_client

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(self, chat_id: int, config: dict) -> str:
        """Start a new autonomous session. Returns confirmation message."""
        is_active = await self.is_active()
        if is_active:
            return "⚠️ Session already running. Use /auto_stop first."

        risk_pct = float(config.get("risk_pct", DEFAULT_RISK_PCT))
        max_pos = int(config.get("max_pos", DEFAULT_MAX_POS))
        interval = int(config.get("interval", DEFAULT_INTERVAL_MIN))

        # Snapshot starting equity
        wallet = await self.bybit.get_wallet_balance(coin="USDT")
        starting_equity = 0.0
        if not wallet.get("error"):
            try:
                starting_equity = float(wallet.get("balance", 0))
            except (KeyError, IndexError, TypeError):
                pass

        session_config = {
            "risk_pct": risk_pct,
            "max_pos": max_pos,
            "interval_min": interval,
        }

        # Persist to Redis
        pipe = self.redis.pipeline()
        pipe.set(REDIS_ACTIVE, "1")
        pipe.set(REDIS_CONFIG, json.dumps(session_config))
        pipe.set(REDIS_START_TIME, str(time.time()))
        pipe.set(REDIS_START_EQUITY, str(starting_equity))
        pipe.delete(REDIS_PROGRESS_TS)
        await pipe.execute()

        # Persist session to DB
        await self._save_session_start(session_config, starting_equity)

        logger.info("asm_started", config=session_config, equity=starting_equity)

        # Spawn the loop
        asyncio.create_task(self._run_loop(chat_id))

        from src.utils.format import fmt, bold, code
        return fmt(
            bold("🤖 AUTONOMOUS SESSION STARTED"),
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "\n💰 Equity: ", code(f"${starting_equity:,.2f}"),
            "\n📊 Risk: ", code(f"{risk_pct}%"), " | Max Positions: ", code(str(max_pos)),
            "\n⏱️ Interval: ", code(f"{interval}m"),
        )

    async def stop(self) -> str:
        """Stop the session. Returns MTM report."""
        if not await self.is_active():
            return "ℹ️ No active session."

        await self.redis.set(REDIS_ACTIVE, "0")
        logger.info("asm_stopping")

        # Generate MTM report
        report = await self._generate_final_report()

        # Cleanup Redis state
        await self._cleanup()

        return report

    async def get_status(self) -> str:
        """Generate current status view (for ASM dashboard)."""
        if not await self.is_active():
            return self._format_inactive_status()

        config = await self._get_config()
        start_time = float(await self.redis.get(REDIS_START_TIME) or time.time())
        start_equity = float(await self.redis.get(REDIS_START_EQUITY) or 0)
        running_sec = time.time() - start_time

        # Live wallet
        wallet = await self.bybit.get_wallet_balance(coin="USDT")
        current_equity = start_equity
        available_cash = 0.0
        if not wallet.get("error"):
            try:
                current_equity = float(wallet.get("balance", 0))
                available_cash = float(wallet.get("available", 0))
                # Bybit testnet: availableToWithdraw returns 0 even with free funds
                if available_cash <= 0:
                    available_cash = current_equity
            except (KeyError, IndexError, TypeError):
                pass

        # Open positions
        positions = await self.bybit.get_positions()
        open_count = 0
        unrealized_pnl = 0.0
        pos_lines = []
        for p in (positions or []):
            size = float(p.get("size", 0) or 0)
            if size <= 0:
                continue
            open_count += 1
            pnl = float(p.get("unrealisedPnl", 0) or 0)
            unrealized_pnl += pnl
            emoji = "🟢" if pnl >= 0 else "🔴"
            side = "L" if p.get("side") == "Buy" else "S"
            pos_lines.append(
                f"  {emoji} <code>{p.get('symbol', '?')}</code> {side} "
                f"| uPnL: <code>${pnl:+,.2f}</code>"
            )

        # Regime
        regime_state = "UNKNOWN"
        try:
            from src.advisory.crypto_regime import CryptoRegimeFilter
            crf = CryptoRegimeFilter(self.orchestrator.mcp)
            regime = await crf.get_current_regime()
            regime_state = regime.get("state", "UNKNOWN") if regime else "UNKNOWN"
        except Exception:
            pass

        # MTM PnL
        realized_pnl = await self._get_session_realized_pnl()
        total_pnl = realized_pnl + unrealized_pnl

        hours = int(running_sec // 3600)
        mins = int((running_sec % 3600) // 60)

        def _pnl(v: float) -> str:
            sign = "+" if v >= 0 else "-"
            return f"{sign}${abs(v):,.2f}"

        from src.utils.format import fmt, bold, code

        parts = [
            bold("🤖 AUTONOMOUS SESSION — ACTIVE"),
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
            bold("💰 Equity: "), code(f"${current_equity:,.2f}"), f" (started ${start_equity:,.2f})\n",
            bold("💵 Available Cash: "), code(f"${available_cash:,.2f}"), "\n",
            bold("📈 Realized PnL: "), code(_pnl(realized_pnl)), "\n",
            bold("📊 Unrealized PnL: "), code(_pnl(unrealized_pnl)), "\n",
            bold("📊 Total MTM: "), code(_pnl(total_pnl)), "\n",
            bold("🎯 Risk: "), code(f"{config.get('risk_pct', DEFAULT_RISK_PCT)}%"), bold(" | Max pos: "), code(str(config.get('max_pos', DEFAULT_MAX_POS))), "\n",
            bold("📡 Regime: "), code(regime_state), "\n",
            bold("⏱️ Running: "), code(f"{hours}h {mins}m"),
        ]

        if pos_lines:
            parts.append("\n")
            parts.append(bold(f"📋 Open Positions ({open_count}):"))
            parts.extend(pos_lines)
        else:
            parts.append("\n📭 No open positions.")

        return fmt(*lines)

    def _format_inactive_status(self) -> str:
        from src.utils.format import fmt, bold
        return fmt(
            bold("🤖 AUTONOMOUS SESSION"),
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
            "\n📭 No active session.\n",
            "\nPress ", bold("▶️ Start"), " to begin.",
        )

    # ── The Loop ───────────────────────────────────────────────

    async def _run_loop(self, chat_id: int):
        """Main autonomous scanning loop. Runs until stopped or crashed."""
        config = await self._get_config()
        interval_sec = config.get("interval_min", DEFAULT_INTERVAL_MIN) * 60
        max_pos = config.get("max_pos", DEFAULT_MAX_POS)

        logger.info("asm_loop_started", config=config)

        while await self.is_active():
            try:
                # 1. Regime gate
                should_pause, regime_msg = await self._check_regime()
                if should_pause:
                    from src.metrics.crypto_metrics import AUTO_SESSION_REGIME_PAUSES
                    AUTO_SESSION_REGIME_PAUSES.inc()
                    if chat_id:
                        await self._send_telegram(chat_id, regime_msg)
                    await asyncio.sleep(3600)
                    continue

                # 2. Capacity check
                open_count = await self._get_open_position_count()
                if open_count >= max_pos:
                    logger.info("asm_at_capacity", open=open_count, max=max_pos)
                    await asyncio.sleep(interval_sec)
                    continue

                # 3. Scan for signals (clear dedup cache so ASM can re-trade same coins)
                from src.agents.orchestrator import _signal_cache
                _signal_cache.clear()
                signals = await self.orchestrator.scan_all_markets("CRYPTO")

                # 4. Execute each signal through existing pipeline
                for signal in signals:
                    if not await self.is_active():
                        break
                    if await self._get_open_position_count() >= max_pos:
                        break
                    await self._execute_signal(signal, chat_id)

                # 5. Progress update (cooldown enforced)
                await self._maybe_send_progress(chat_id)

            except Exception as e:
                logger.critical("asm_loop_error", error=str(e))
                if chat_id:
                    await self._send_telegram(chat_id, f"🚨 <b>Loop Error:</b> <code>{e}</code>")

            await asyncio.sleep(interval_sec)

        logger.info("asm_loop_exited")

    # ── Execution ──────────────────────────────────────────────

    async def _execute_signal(self, signal: dict, chat_id: int):
        """Execute a single signal through lock → risk → SOR."""
        from src.risk.distributed_lock import acquire_execution_lock, release_execution_lock

        ticker = signal.get("ticker", "?")
        direction = signal.get("direction", "?")

        acquired = await acquire_execution_lock(self.redis, ticker)
        if not acquired:
            logger.info("asm_lock_busy", ticker=ticker)
            return

        try:
            from src.metrics.crypto_metrics import AUTO_SESSION_TRADES_TOTAL

            # Inject cash-based sizing into signal BEFORE risk gate
            signal = await self._apply_cash_sizing(signal)
            if "_cash_sized_qty" in signal:
                signal["qty"] = signal["_cash_sized_qty"]

            # Execute through existing orchestrator pipeline
            result = await self.orchestrator._auto_execute_crypto(
                [signal], regime=None
            )

            if result:
                status = result[0].get("status", "UNKNOWN")
                if status == "EXECUTED":
                    AUTO_SESSION_TRADES_TOTAL.labels(result="executed").inc()
                    logger.info("asm_trade_executed", ticker=ticker, direction=direction)
                else:
                    AUTO_SESSION_TRADES_TOTAL.labels(result="rejected").inc()
        except Exception as e:
            logger.error("asm_execute_failed", ticker=ticker, error=str(e))
        finally:
            await release_execution_lock(self.redis, ticker)

    async def _apply_cash_sizing(self, signal: dict) -> dict:
        """Override position sizing to use available cash, not total equity."""
        config = await self._get_config()
        risk_pct = config.get("risk_pct", DEFAULT_RISK_PCT) / 100

        wallet = await self.bybit.get_wallet_balance(coin="USDT")
        if wallet.get("error"):
            return signal

        try:
            available_cash = float(wallet.get("available", 0))
            # Bybit testnet: availableToWithdraw returns 0 even with free funds
            if available_cash <= 0:
                available_cash = float(wallet.get("balance", 0))
        except (KeyError, IndexError, TypeError):
            return signal

        if available_cash <= 0:
            return signal

        # Risk amount in USD
        risk_amount = available_cash * risk_pct

        # SL distance from signal
        entry = float(signal.get("entry_price", 0) or 0)
        sl = float(signal.get("stop_loss", 0) or 0)
        if entry <= 0 or sl <= 0 or entry == sl:
            return signal

        sl_distance_pct = abs(entry - sl) / entry
        if sl_distance_pct <= 0:
            return signal

        # Position size in USD
        position_value = risk_amount / sl_distance_pct
        # Convert to qty
        qty = position_value / entry

        signal["_cash_risk_amount"] = risk_amount
        signal["_cash_position_value"] = position_value
        signal["_cash_sized_qty"] = round(qty, 6)
        # Override risk gate's max position cap — ASM uses full balance
        signal["_override_max_position_pct"] = 1.0

        logger.debug(
            "asm_cash_sizing",
            ticker=signal.get("ticker"),
            available_cash=available_cash,
            risk_amount=risk_amount,
            position_value=position_value,
            qty=qty,
        )
        return signal

    # ── Regime Gate ────────────────────────────────────────────

    async def _check_regime(self) -> tuple[bool, str]:
        """Returns (should_pause, alert_message).
        Regime gate disabled for ASM — risk gates + SL handle protection.
        """
        return False, ""

    # ── MTM Report ─────────────────────────────────────────────

    async def _generate_final_report(self) -> str:
        """Generate institutional-grade MTM performance report."""
        start_time = float(await self.redis.get(REDIS_START_TIME) or time.time())
        start_equity = float(await self.redis.get(REDIS_START_EQUITY) or 0)
        config = await self._get_config()

        # Realized PnL from DB
        realized_pnl, wins, losses, total_trades = await self._get_session_stats()

        # Live MTM for open positions
        positions = await self.bybit.get_positions()
        unrealized_pnl = 0.0
        open_count = 0
        for p in (positions or []):
            size = float(p.get("size", 0) or 0)
            if size > 0:
                open_count += 1
                unrealized_pnl += float(p.get("unrealisedPnl", 0) or 0)

        total_pnl = realized_pnl + unrealized_pnl
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
        running_sec = time.time() - start_time
        hours = int(running_sec // 3600)
        mins = int((running_sec % 3600) // 60)

        # Update DB session record
        await self._save_session_end(total_trades, wins, losses, realized_pnl, unrealized_pnl)

        # Update Prometheus
        try:
            from src.metrics.crypto_metrics import (
                AUTO_SESSION_ACTIVE, AUTO_SESSION_REALIZED_PNL, AUTO_SESSION_UNREALIZED_PNL
            )
            AUTO_SESSION_ACTIVE.set(0)
            AUTO_SESSION_REALIZED_PNL.set(realized_pnl)
            AUTO_SESSION_UNREALIZED_PNL.set(unrealized_pnl)
        except Exception:
            pass

        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"

        def _pnl(v: float) -> str:
            """Format PnL with sign before $: +$1.23 or -$0.50"""
            sign = "+" if v >= 0 else "-"
            return f"{sign}${abs(v):,.2f}"

        from src.utils.format import fmt, bold, code

        report = fmt(
            bold("🏁 AUTONOMOUS SESSION COMPLETED"),
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
            "\n", bold("💰 Financial Summary"),
            "\n  • Starting Equity: ", code(f"${start_equity:,.2f}"),
            "\n  • Realized PnL: ", code(_pnl(realized_pnl)),
            "\n  • Unrealized PnL: ", code(_pnl(unrealized_pnl)),
            f"\n  • {pnl_emoji} Total MTM: ", code(_pnl(total_pnl)),
            "\n\n", bold("📊 Performance"),
            "\n  • Trades: ", code(str(total_trades)), " | Win Rate: ", code(f"{win_rate:.1f}%"), f" ({wins}W / {losses}L)",
            "\n  • Open Positions: ", code(str(open_count)),
            "\n\n", bold("⏱️ Duration: "), code(f"{hours}h {mins}m"),
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        )
        return report

    # ── Helpers ────────────────────────────────────────────────

    async def is_active(self) -> bool:
        val = await self.redis.get(REDIS_ACTIVE)
        return val in ("1", b"1")

    async def _get_config(self) -> dict:
        raw = await self.redis.get(REDIS_CONFIG)
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
        return {"risk_pct": DEFAULT_RISK_PCT, "max_pos": DEFAULT_MAX_POS, "interval_min": DEFAULT_INTERVAL_MIN}

    async def _get_open_position_count(self) -> int:
        positions = await self.bybit.get_positions()
        return sum(1 for p in (positions or []) if float(p.get("size", 0) or 0) > 0)

    async def _get_session_realized_pnl(self) -> float:
        """Get realized PnL from closed trades during this session."""
        try:
            start_time = float(await self.redis.get(REDIS_START_TIME) or 0)
            if start_time <= 0:
                return 0.0
            from src.models.database import async_session
            from src.models.tables import ClosedPaperTrade
            from sqlalchemy import select, func
            async with async_session() as session:
                result = await session.execute(
                    select(func.coalesce(func.sum(ClosedPaperTrade.realized_pnl), 0))
                    .where(
                        ClosedPaperTrade.market == "CRYPTO",
                        ClosedPaperTrade.exit_date >= datetime.fromtimestamp(start_time, tz=timezone.utc),
                    )
                )
                return float(result.scalar() or 0.0)
        except Exception as e:
            logger.debug("asm_realized_pnl_query_failed", error=str(e))
            return 0.0

    async def _get_session_stats(self) -> tuple[float, int, int, int]:
        """Get (realized_pnl, wins, losses, total_trades) from DB."""
        try:
            start_time = float(await self.redis.get(REDIS_START_TIME) or 0)
            if start_time <= 0:
                return 0.0, 0, 0, 0
            from src.models.database import async_session
            from src.models.tables import ClosedPaperTrade
            from sqlalchemy import select, func
            async with async_session() as session:
                result = await session.execute(
                    select(
                        func.coalesce(func.sum(ClosedPaperTrade.realized_pnl), 0),
                        func.count(ClosedPaperTrade.id),
                    )
                    .where(
                        ClosedPaperTrade.market == "CRYPTO",
                        ClosedPaperTrade.exit_date >= datetime.fromtimestamp(start_time, tz=timezone.utc),
                    )
                )
                row = result.one()
                total = row[1]
                win_result = await session.execute(
                    select(func.count(ClosedPaperTrade.id))
                    .where(
                        ClosedPaperTrade.market == "CRYPTO",
                        ClosedPaperTrade.exit_date >= datetime.fromtimestamp(start_time, tz=timezone.utc),
                        ClosedPaperTrade.realized_pnl > 0,
                    )
                )
                wins = win_result.scalar() or 0
                return float(row[0]), wins, total - wins, total
        except Exception as e:
            logger.debug("asm_stats_query_failed", error=str(e))
            return 0.0, 0, 0, 0

    async def _maybe_send_progress(self, chat_id: int):
        """Send progress update if cooldown has elapsed."""
        if not chat_id:
            return
        last = await self.redis.get(REDIS_PROGRESS_TS)
        now = time.time()
        if last and (now - float(last)) < PROGRESS_COOLDOWN_SEC:
            return
        status = await self.get_status()
        await self._send_telegram(chat_id, status)
        await self.redis.set(REDIS_PROGRESS_TS, str(now))

    async def _send_telegram(self, chat_id: int, text: str):
        """Send message via Telegram HTTP API."""
        try:
            import httpx
            from src.config import settings
            token = settings.CRYPTO_TELEGRAM_TOKEN or settings.TELEGRAM_TOKEN
            if not token:
                return
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                )
        except Exception as e:
            logger.warning("asm_telegram_send_failed", error=str(e))

    async def _cleanup(self):
        """Remove all Redis keys for this session."""
        await self.redis.delete(
            REDIS_ACTIVE, REDIS_CONFIG, REDIS_START_TIME,
            REDIS_START_EQUITY, REDIS_PROGRESS_TS,
        )

    # ── DB Persistence ─────────────────────────────────────────

    async def _save_session_start(self, config: dict, starting_equity: float):
        try:
            from src.models.database import async_session
            from src.models.tables import CryptoAutoSession
            async with async_session() as session:
                session.add(CryptoAutoSession(
                    started_at=datetime.now(timezone.utc),
                    config=config,
                    starting_equity=starting_equity,
                    status="RUNNING",
                ))
                await session.commit()
        except Exception as e:
            logger.error("asm_session_save_failed", error=str(e))

    async def _save_session_end(self, total_trades, wins, losses, realized_pnl, unrealized_pnl):
        try:
            from src.models.database import async_session
            from src.models.tables import CryptoAutoSession
            from sqlalchemy import update
            async with async_session() as session:
                await session.execute(
                    update(CryptoAutoSession)
                    .where(CryptoAutoSession.status == "RUNNING")
                    .values(
                        ended_at=datetime.now(timezone.utc),
                        total_trades=total_trades,
                        wins=wins,
                        losses=losses,
                        realized_pnl=realized_pnl,
                        unrealized_pnl=unrealized_pnl,
                        status="STOPPED",
                    )
                )
                await session.commit()
        except Exception as e:
            logger.error("asm_session_end_save_failed", error=str(e))
