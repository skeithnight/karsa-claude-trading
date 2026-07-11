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
REDIS_PAUSED = "karsa:auto:state:paused"
REDIS_CONFIG = "karsa:auto:config"
REDIS_START_TIME = "karsa:auto:start_time"
REDIS_START_EQUITY = "karsa:auto:start_equity"
REDIS_PROGRESS_TS = "karsa:auto:last_progress_ts"
REDIS_PEAK_EQUITY = "karsa:auto:peak_equity"
REDIS_MAX_DD = "karsa:auto:max_dd"

# Default config
DEFAULT_RISK_PCT = 1.5
DEFAULT_MAX_POS = 3
DEFAULT_INTERVAL_MIN = 15
PROGRESS_COOLDOWN_SEC = 1800  # 30 min
REDIS_COOLDOWN_PREFIX = "karsa:auto:cooldown:"  # per-ticker re-entry cooldown
REENTRY_COOLDOWN_SEC = 7200  # 2 hours after position close
MAX_CONSECUTIVE_SCAN_FAILURES = 5
DEFAULT_MAX_DD_PCT = 10.0  # auto-stop if DD exceeds this %


class AutonomousSessionManager:
    """Autonomous trading loop — state machine backed by Redis."""

    def __init__(self, orchestrator, redis, bybit_client):
        self.orchestrator = orchestrator
        self.redis = redis
        self.bybit = bybit_client
        self._scan_lock = asyncio.Lock()

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(self, chat_id: int, config: dict) -> str:
        """Start a new autonomous session. Returns confirmation message."""
        is_active = await self.is_active()
        if is_active:
            return "⚠️ Session already running. Use /auto_stop first."

        # Check if halt is active — refuse to start if halted (P0 safety)
        try:
            from src.risk import emergency
            if await emergency.is_global_halt():
                return (
                    "🚨 <b>HALT ACTIVE</b> — Session cannot start.\n"
                    "Previous session was halted due to risk limits.\n"
                    "Review your account, then use /clear_halt to reset."
                )
        except Exception:
            pass

        risk_pct = float(config.get("risk_pct", DEFAULT_RISK_PCT))
        max_pos = int(config.get("max_pos", DEFAULT_MAX_POS))
        interval = int(config.get("interval", DEFAULT_INTERVAL_MIN))
        duration_min = int(config.get("duration_min", 0))

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
            "duration_min": duration_min,
        }

        # Persist to Redis
        try:
            pipe = self.redis.pipeline()
            pipe.set(REDIS_ACTIVE, "1")
            pipe.set(REDIS_PAUSED, "0")
            pipe.set(REDIS_CONFIG, json.dumps(session_config))
            pipe.set(REDIS_START_TIME, str(time.time()))
            pipe.set(REDIS_START_EQUITY, str(starting_equity))
            pipe.set(REDIS_PEAK_EQUITY, str(starting_equity))
            pipe.set(REDIS_MAX_DD, "0")
            pipe.delete(REDIS_PROGRESS_TS)
            results = await pipe.execute()
            logger.info("asm_redis_written", keys=[REDIS_ACTIVE, REDIS_CONFIG], results=results)
        except Exception as e:
            logger.error("asm_redis_write_failed", error=str(e))
            # Fallback: direct set
            await self.redis.set(REDIS_ACTIVE, "1")
            await self.redis.set(REDIS_PAUSED, "0")
            await self.redis.set(REDIS_CONFIG, json.dumps(session_config))
            logger.info("asm_redis_fallback_written")

        # Persist session to DB
        await self._save_session_start(session_config, starting_equity)

        # Update Prometheus — ASM started
        try:
            from src.metrics.crypto_metrics import (
                AUTO_SESSION_ACTIVE, AUTO_SESSION_CASH_USD,
                AUTO_SESSION_REALIZED_PNL, AUTO_SESSION_UNREALIZED_PNL,
            )
            AUTO_SESSION_ACTIVE.set(1)
            AUTO_SESSION_CASH_USD.set(starting_equity)
            AUTO_SESSION_REALIZED_PNL.set(0)
            AUTO_SESSION_UNREALIZED_PNL.set(0)
            from src.metrics.crypto_metrics import record_asm_state
            record_asm_state(1)  # idle — no positions yet
        except Exception:
            pass

        logger.info("asm_started", config=session_config, equity=starting_equity)

        # Spawn the loop
        asyncio.create_task(self._run_loop(chat_id))

        from src.utils.format import fmt, bold, code
        dur_str = "Unlimited" if duration_min == 0 else f"{duration_min // 60}h {duration_min % 60}m" if duration_min >= 60 else f"{duration_min}m"
        return fmt(
            bold("🤖 AUTONOMOUS SESSION STARTED"),
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "\n💰 Equity: ", code(f"${starting_equity:,.2f}"),
            "\n📊 Risk: ", code(f"{risk_pct}%"), " | Max Positions: ", code(str(max_pos)),
            "\n⏱️ Interval: ", code(f"{interval}m"), " | Duration: ", code(dur_str),
        )

    async def _clear_stale_emergency(self) -> None:
        """Clear leftover emergency/halt keys so a fresh ASM session isn't blocked."""
        from src.risk import emergency
        cleared = []
        try:
            if await emergency.is_active():
                await emergency.deactivate(operator="asm_auto_start")
                cleared.append("emergency_stop")
        except Exception:
            pass
        try:
            if await emergency.is_global_halt():
                await emergency.deactivate_global_halt(operator="asm_auto_start")
                cleared.append("global_halt")
        except Exception:
            pass
        if cleared:
            logger.warning("asm_cleared_stale_emergency", cleared=cleared)

    async def reconcile_state(self) -> str:
        """Reconcile Bybit open positions/orders with Redis/DB state on startup.

        Called on bot startup if an active session is found in Redis.
        Syncs orphan positions and alerts the user.
        """
        if not await self.is_active():
            return ""

        try:
            # Get Bybit open positions
            bybit_positions = await self.bybit.get_positions()
            open_bybit = [p for p in (bybit_positions or []) if float(p.get("size", 0)) > 0]

            # Get positions tracked in DB
            from src.models.database import async_session
            from src.models.tables import CryptoPosition
            from sqlalchemy import select
            async with async_session() as db:
                result = await db.execute(
                    select(CryptoPosition).where(CryptoPosition.status == "OPEN")
                )
                db_positions = {p.ticker: p for p in result.scalars().all()}

            # Find orphans: on Bybit but not in DB
            bybit_symbols = {p.get("symbol", "") for p in open_bybit}
            orphan_symbols = bybit_symbols - set(db_positions.keys())

            if orphan_symbols:
                logger.warning("reconciliation_orphans_found", orphans=list(orphan_symbols))
                return (
                    f"⚠️ <b>Reconciliation Alert</b>\n"
                    f"Found {len(orphan_symbols)} orphan position(s) on Bybit "
                    f"not tracked in DB: {', '.join(orphan_symbols)}\n"
                    f"These positions will remain open. Use /control to manage them."
                )

            # Find DB positions not on Bybit (stale DB records)
            stale_symbols = set(db_positions.keys()) - bybit_symbols
            if stale_symbols:
                logger.info("reconciliation_stale_found", stale=list(stale_symbols))
                # Mark stale DB records as CLOSED.
                # MUST re-load each object inside the new session — objects from the
                # previous session are detached and not tracked by this session's
                # identity map. Mutating them then calling commit() is a no-op.
                from datetime import datetime as _dt
                async with async_session() as db:
                    for sym in stale_symbols:
                        live_pos = await db.get(CryptoPosition, db_positions[sym].id)
                        if live_pos:
                            live_pos.status = "CLOSED"
                            live_pos.last_synced_at = _dt.utcnow()
                    await db.commit()

            logger.info("reconciliation_complete", orphans=len(orphan_symbols), stale=len(stale_symbols))
            return ""

        except Exception as e:
            logger.error("reconciliation_failed", error=str(e))
            return f"⚠️ Reconciliation check failed: {e}"

    async def pause(self) -> str:
        """Pause scanning loop, keeping existing positions intact."""
        if not await self.is_active():
            return "ℹ️ No active session to pause."
        await self.redis.set(REDIS_PAUSED, "1")
        logger.info("asm_paused")
        return "⏸ <b>Autonomous Session Paused</b>\nScanning loop frozen. Open positions remain active."

    async def resume(self) -> str:
        """Resume scanning loop from pause."""
        if not await self.is_active():
            return "ℹ️ No active session to resume."
        await self.redis.set(REDIS_PAUSED, "0")
        logger.info("asm_resumed")
        return "▶️ <b>Autonomous Session Resumed</b>\nScanning loop active."

    async def stop(self) -> str:
        """Stop the session. Returns MTM report."""
        if not await self.is_active():
            return "ℹ️ No active session."

        await self.redis.set(REDIS_ACTIVE, "0")
        await self.redis.set(REDIS_PAUSED, "0")
        logger.info("asm_stopping")

        # Update Prometheus — ASM stopped
        try:
            from src.metrics.crypto_metrics import AUTO_SESSION_ACTIVE, record_asm_state
            AUTO_SESSION_ACTIVE.set(0)
            record_asm_state(0)
        except Exception:
            pass

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
            pnl = float(p.get("unrealized_pnl", 0) or 0)
            unrealized_pnl += pnl
            emoji = "🟢" if pnl >= 0 else "🔴"
            side = "L" if p.get("side") == "Buy" else "S"
            symbol = p.get("symbol", "?")
            # Format as plain text (not raw HTML tags)
            pos_lines.append(
                f"  {emoji} {symbol} {side} | uPnL: ${pnl:+,.2f}"
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
            fmt(bold("🤖 AUTONOMOUS SESSION — ACTIVE"),
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
            bold("💰 Equity: "), code(f"${current_equity:,.2f}"), f" (started ${start_equity:,.2f})\n",
            bold("💵 Available Cash: "), code(f"${available_cash:,.2f}"), "\n",
            bold("📈 Realized PnL: "), code(_pnl(realized_pnl)), "\n",
            bold("📊 Unrealized PnL: "), code(_pnl(unrealized_pnl)), "\n",
            bold("📊 Total MTM: "), code(_pnl(total_pnl)), "\n",
            bold("🎯 Risk: "), code(f"{config.get('risk_pct', DEFAULT_RISK_PCT)}%"), bold(" | Max pos: "), code(str(config.get('max_pos', DEFAULT_MAX_POS))), "\n",
            bold("📡 Regime: "), code(regime_state), "\n",
            bold("⏱️ Running: "), code(f"{hours}h {mins}m")),
        ]

        if pos_lines:
            parts.append("\n")
            parts.append(fmt(bold(f"📋 Open Positions ({open_count}):")))
            parts.extend(pos_lines)
        else:
            parts.append("\n📭 No open positions.")

        return fmt(*parts)

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

        # Max duration check
        max_duration_min = config.get("duration_min", 0)
        start_ts = time.time()
        consecutive_scan_failures = 0

        while await self.is_active():
            if await self.is_paused():
                logger.info("asm_loop_paused")
                await asyncio.sleep(15)
                continue

            # Duration check
            if max_duration_min > 0:
                elapsed_min = (time.time() - start_ts) / 60
                if elapsed_min >= max_duration_min:
                    logger.info("asm_duration_reached", elapsed=f"{elapsed_min:.0f}min", limit=f"{max_duration_min}min")
                    if chat_id:
                        await self._send_telegram(chat_id, f"⏰ <b>Duration reached</b> ({max_duration_min}min). Stopping session.")
                    break
            try:
                # Update ASM metrics each iteration
                try:
                    from src.metrics.crypto_metrics import (
                        AUTO_SESSION_CASH_USD, AUTO_SESSION_UNREALIZED_PNL,
                    )
                    wallet = await self.bybit.get_wallet_balance()
                    cash = float(wallet.get("balance", 0))
                    AUTO_SESSION_CASH_USD.set(cash)
                    # Unrealized PnL from open positions
                    positions = await self.bybit.get_positions()
                    unrl = sum(float(p.get("unrealized_pnl", 0)) for p in positions)
                    AUTO_SESSION_UNREALIZED_PNL.set(unrl)
                    # Per-position metrics for dashboard table
                    from src.metrics.crypto_metrics import (
                        POSITION_PNL, POSITION_ENTRY_PRICE, POSITION_MARK_PRICE,
                        POSITION_SIZE, POSITION_LEVERAGE, OPEN_POSITIONS,
                        POSITION_DATA, POSITION_AGE_HOURS, FUNDING_COST,
                    )
                    OPEN_POSITIONS.set(len(positions))
                    # Session performance metrics
                    await self._update_metrics(cash, positions)
                    # ponytail: keep old metrics + emit combined for Grafana single-query table
                    for pos in positions:
                        t = pos.get("ticker", "?")
                        s = pos.get("side", "?")
                        POSITION_PNL.labels(ticker=t, side=s).set(
                            float(pos.get("unrealized_pnl", 0)))
                        POSITION_ENTRY_PRICE.labels(ticker=t, side=s).set(
                            float(pos.get("entry_price", 0)))
                        POSITION_MARK_PRICE.labels(ticker=t, side=s).set(
                            float(pos.get("current_price", 0)))
                        POSITION_SIZE.labels(ticker=t, side=s).set(
                            float(pos.get("size", 0)))
                        POSITION_LEVERAGE.labels(ticker=t, side=s).set(
                            float(pos.get("leverage", 1)))
                        POSITION_DATA.labels(ticker=t, side=s, field="uPnL").set(
                            float(pos.get("unrealized_pnl", 0)))
                        POSITION_DATA.labels(ticker=t, side=s, field="entry_price").set(
                            float(pos.get("entry_price", 0)))
                        POSITION_DATA.labels(ticker=t, side=s, field="mark_price").set(
                            float(pos.get("current_price", 0)))
                        POSITION_DATA.labels(ticker=t, side=s, field="size").set(
                            float(pos.get("size", 0)))
                        POSITION_DATA.labels(ticker=t, side=s, field="leverage").set(
                            float(pos.get("leverage", 1)))
                        # Position age
                        opened_at = pos.get("opened_at")
                        if opened_at:
                            try:
                                from datetime import datetime, timezone
                                if isinstance(opened_at, str):
                                    opened_at = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                                age_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                                POSITION_AGE_HOURS.labels(ticker=t).set(round(age_hours, 1))
                            except Exception:
                                pass
                        # Funding cost estimate
                        funding_rate = float(pos.get("funding_rate", 0) or 0)
                        if funding_rate != 0:
                            position_value = float(pos.get("size", 0)) * float(pos.get("current_price", 0))
                            funding_cost_usd = position_value * abs(funding_rate)
                            FUNDING_COST.labels(ticker=t).set(round(funding_cost_usd, 4))
                except Exception as e:
                    logger.debug("asm_metrics_update_skipped", error=str(e))

                # 0b. Emergency stop check (circuit breaker triggers set this)
                try:
                    from src.risk import emergency
                    if await emergency.is_active():
                        logger.info("asm_emergency_active", msg="skipping iteration")
                        await asyncio.sleep(interval_sec)
                        continue
                except Exception as e:
                    logger.debug("asm_emergency_check_failed", error=str(e))

                # 0c. Max drawdown auto-stop
                try:
                    peak = float(await self.redis.get(REDIS_PEAK_EQUITY) or 0)
                    wallet_now = await self.bybit.get_wallet_balance()
                    current_eq = float(wallet_now.get("total_equity", wallet_now.get("balance", 0)))
                    if peak > 0 and current_eq > 0:
                        dd_pct = (peak - current_eq) / peak * 100
                        max_dd_pct = config.get("max_dd_pct", DEFAULT_MAX_DD_PCT)
                        if dd_pct >= max_dd_pct:
                            logger.warning("asm_max_dd_breach", dd_pct=round(dd_pct, 2), limit=max_dd_pct)
                            if chat_id:
                                await self._send_telegram(chat_id, f"🚨 <b>Max DD breached</b> ({dd_pct:.1f}% ≥ {max_dd_pct}%). Stopping session.")
                            break
                except Exception as e:
                    logger.debug("asm_dd_check_failed", error=str(e))

                # 1. Regime gate — dynamic re-evaluation for BEAR (10m instead of 60m)
                should_pause, regime_msg = await self._check_regime()
                if should_pause:
                    from src.metrics.crypto_metrics import AUTO_SESSION_REGIME_PAUSES
                    AUTO_SESSION_REGIME_PAUSES.inc()
                    if chat_id:
                        await self._send_telegram(chat_id, regime_msg)
                    # Dynamic: re-check regime every 10 minutes instead of fixed 60m
                    for _ in range(6):  # 6 x 10min = 60min max
                        await asyncio.sleep(600)
                        if not await self.is_active():
                            break
                        # Re-check regime
                        should_pause_again, _ = await self._check_regime()
                        if not should_pause_again:
                            logger.info("asm_regime_shifted_resuming")
                            break
                    continue

                # Acquire scan lock to prevent overlapping iterations
                if self._scan_lock.locked():
                    logger.info("asm_scan_locked_skipping")
                    await asyncio.sleep(interval_sec)
                    continue

                async with self._scan_lock:

                    # 2. Capacity check
                    open_count = await self._get_open_position_count()
                    if open_count >= max_pos:
                        logger.info("asm_at_capacity", open=open_count, max=max_pos)
                        await asyncio.sleep(interval_sec)
                        continue

                    # 3. Scan for signals (TTL-based dedup — 4h prevents re-entry into same setup)
                    from src.agents.orchestrator import _signal_cache
                    _signal_cache.clear()
                    # TTL dedup: processed signals get 4-hour expiry via Redis
                    # Prevents re-entry while allowing new breakouts
                    # Use _scan_crypto_parallel directly — skip scan_all_markets which auto-executes with regime gate
                    from src.advisory.crypto_regime import CryptoRegimeFilter
                    crf = CryptoRegimeFilter(self.orchestrator.mcp)
                    crypto_regime = await crf.get_current_regime()
                    try:
                        signals = await self.orchestrator._scan_crypto_parallel(crypto_regime)
                        consecutive_scan_failures = 0
                    except Exception as scan_err:
                        consecutive_scan_failures += 1
                        backoff = min(interval_sec * (2 ** consecutive_scan_failures), 3600)
                        logger.warning("asm_scan_failed", error=str(scan_err), failures=consecutive_scan_failures, backoff_sec=backoff)
                        if consecutive_scan_failures >= MAX_CONSECUTIVE_SCAN_FAILURES and chat_id:
                            await self._send_telegram(chat_id, f"⚠️ <b>{consecutive_scan_failures} consecutive scan failures</b>. Backing off {backoff // 60}min.")
                        await asyncio.sleep(backoff)
                        continue
                    logger.info("asm_scan_result", signal_count=len(signals), tickers=[s.get("ticker","?") for s in signals])

                    # 4. Execute each signal through existing pipeline
                    for signal in signals:
                        if not await self.is_active():
                            break
                        if await self._get_open_position_count() >= max_pos:
                            break
                        await self._execute_signal(signal, chat_id, regime=crypto_regime)

                    # 5. Progress update (cooldown enforced)
                    await self._maybe_send_progress(chat_id)
                    await self.redis.set(REDIS_PROGRESS_TS, str(time.time()))
                    from src.metrics.crypto_metrics import update_asm_next_scan
                    update_asm_next_scan(interval_sec)

            except Exception as e:
                logger.critical("asm_loop_error", error=str(e))
                if chat_id:
                    await self._send_telegram(chat_id, f"🚨 <b>Loop Error:</b> <code>{e}</code>")

            await asyncio.sleep(interval_sec)

        logger.info("asm_loop_exited")

    # ── Execution ──────────────────────────────────────────────

    async def _execute_signal(self, signal: dict, chat_id: int, regime: dict | None = None):
        """Execute a single signal through lock → risk → SOR."""
        from src.risk.distributed_lock import acquire_execution_lock, release_execution_lock

        ticker = signal.get("ticker", "?")
        direction = signal.get("direction", "?")

        # Re-entry cooldown check
        cooldown_key = f"{REDIS_COOLDOWN_PREFIX}{ticker}"
        if await self.redis.exists(cooldown_key):
            logger.info("asm_cooldown_skip", ticker=ticker)
            return

        acquired = await acquire_execution_lock(self.redis, ticker)
        if not acquired:
            logger.info("asm_lock_busy", ticker=ticker)
            return

        try:
            from src.metrics.crypto_metrics import AUTO_SESSION_TRADES_TOTAL

            # Inject entry_price from Bybit if LLM didn't provide it
            if not signal.get("entry_price") or signal["entry_price"] <= 0:
                try:
                    ticker_data = await self.bybit.get_ticker(ticker)
                    signal["entry_price"] = float(ticker_data.get("price", 0))
                except Exception:
                    pass

            # Inject cash-based sizing into signal BEFORE risk gate
            signal = await self._apply_cash_sizing(signal)
            if "_cash_sized_qty" in signal:
                signal["qty"] = signal["_cash_sized_qty"]

            # Execute through existing orchestrator pipeline — reuse cached regime
            if regime is None:
                from src.advisory.crypto_regime import CryptoRegimeFilter
                regime_filter = CryptoRegimeFilter(self.orchestrator.mcp)
                regime = await regime_filter.get_current_regime()
            result = await self.orchestrator._auto_execute_crypto(
                [signal], regime=regime
            )

            if result:
                status = result[0].get("status", "UNKNOWN")
                if status == "EXECUTED":
                    AUTO_SESSION_TRADES_TOTAL.labels(result="executed").inc()
                    logger.info("asm_trade_executed", ticker=ticker, direction=direction)
                    await self.redis.set(f"{REDIS_COOLDOWN_PREFIX}{ticker}", "1", ex=REENTRY_COOLDOWN_SEC)
                else:
                    AUTO_SESSION_TRADES_TOTAL.labels(result="rejected").inc()
                # TTL dedup: mark signal as processed with 4-hour expiry
                signal_hash = f"{ticker}:{direction}"
                await self.redis.set(f"asm:signal_dedup:{signal_hash}", "1", ex=14400)
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

        # Always set override — even if sizing fails, ASM needs risk gate bypass
        signal["_override_max_position_pct"] = 1.0
        signal["_override_leverage"] = 5  # ASM uses 5x to meet Bybit $5 minimum

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

        Previously paused on PURE_DEAD_CHOP, but individual coins can still
        have good setups (FULL_ALIGNMENT, SQUEEZE_ALERT, TREND_BULL) even when
        BTC global regime is dead chop. Now only pauses on extreme fear (<15)
        or when the global regime AND all coin regimes are hostile.
        """
        try:
            from src.advisory.crypto_regime import CryptoRegimeFilter
            crf = CryptoRegimeFilter(self.orchestrator.mcp)
            regime = await crf.get_current_regime()
            state = regime.get("state", "UNKNOWN")
            fear_greed = regime.get("fear_greed", 50)

            # Only pause on extreme market fear
            if fear_greed is not None and fear_greed < 15:
                return True, f"⏸️ <b>ASM paused</b> — extreme fear ({fear_greed}). Re-checking every 10min."

            return False, ""
        except Exception as e:
            logger.warning("asm_regime_check_failed", error=str(e))
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
                unrealized_pnl += float(p.get("unrealized_pnl", 0) or 0)

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
                AUTO_SESSION_ACTIVE, AUTO_SESSION_REALIZED_PNL, AUTO_SESSION_UNREALIZED_PNL,
                DAILY_PNL_USD
            )
            AUTO_SESSION_ACTIVE.set(0)
            AUTO_SESSION_REALIZED_PNL.set(realized_pnl)
            AUTO_SESSION_UNREALIZED_PNL.set(unrealized_pnl)
            DAILY_PNL_USD.set(realized_pnl)
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

    async def is_paused(self) -> bool:
        val = await self.redis.get(REDIS_PAUSED)
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
                        ClosedPaperTrade.exit_date >= datetime.fromtimestamp(start_time, tz=timezone.utc).replace(tzinfo=None),
                    )
                )
                return float(result.scalar() or 0.0)
        except Exception as e:
            logger.debug("asm_realized_pnl_query_failed", error=str(e))
            return 0.0

    async def _get_session_metrics(self) -> dict:
        """Single-session replacement for _get_session_stats + _get_profit_factor.

        Opens ONE DB session for all three stats queries, reducing connection
        checkouts from 3-4 per ASM loop iteration down to 1.
        """
        start_time = float(await self.redis.get(REDIS_START_TIME) or 0)
        if start_time <= 0:
            return {"realized_pnl": 0.0, "wins": 0, "losses": 0, "total": 0, "profit_factor": 0.0}
        try:
            from src.models.database import async_session
            from src.models.tables import ClosedPaperTrade
            from sqlalchemy import select, func
            since = datetime.fromtimestamp(start_time, tz=timezone.utc).replace(tzinfo=None)
            async with async_session() as session:
                # Query 1: totals + gross profit/loss — all in one SELECT
                stats_result = await session.execute(
                    select(
                        func.coalesce(func.sum(ClosedPaperTrade.realized_pnl), 0),
                        func.count(ClosedPaperTrade.id),
                        func.coalesce(func.sum(func.greatest(ClosedPaperTrade.realized_pnl, 0)), 0),
                        func.coalesce(func.sum(func.greatest(-ClosedPaperTrade.realized_pnl, 0)), 0),
                    ).where(
                        ClosedPaperTrade.market == "CRYPTO",
                        ClosedPaperTrade.exit_date >= since,
                    )
                )
                row = stats_result.one()
                total_pnl = float(row[0])
                total_trades = row[1]
                gross_profit = float(row[2])
                gross_loss = float(row[3])

                # Query 2: win count (same session — same connection)
                win_result = await session.execute(
                    select(func.count(ClosedPaperTrade.id)).where(
                        ClosedPaperTrade.market == "CRYPTO",
                        ClosedPaperTrade.exit_date >= since,
                        ClosedPaperTrade.realized_pnl > 0,
                    )
                )
                wins = win_result.scalar() or 0

            losses = total_trades - wins
            if gross_loss <= 0:
                profit_factor = 99.9 if gross_profit > 0 else 0.0
            else:
                profit_factor = round(gross_profit / gross_loss, 2)

            return {
                "realized_pnl": total_pnl,
                "wins": wins,
                "losses": losses,
                "total": total_trades,
                "profit_factor": profit_factor,
            }
        except Exception as e:
            logger.debug("asm_session_metrics_failed", error=str(e))
            return {"realized_pnl": 0.0, "wins": 0, "losses": 0, "total": 0, "profit_factor": 0.0}

    # Keep legacy methods as thin wrappers so any external callers aren't broken
    async def _get_session_stats(self) -> tuple[float, int, int, int]:
        m = await self._get_session_metrics()
        return m["realized_pnl"], m["wins"], m["losses"], m["total"]

    async def _get_profit_factor(self) -> float:
        m = await self._get_session_metrics()
        return m["profit_factor"]

    async def _update_metrics(self, current_equity: float, positions: list[dict]):
        """Publish session performance metrics to Prometheus."""
        try:
            start_equity = float(await self.redis.get(REDIS_START_EQUITY) or current_equity)

            from src.metrics.crypto_metrics import (
                SESSION_RETURN_PCT, MAX_DRAWDOWN_PCT, PROFIT_FACTOR,
                TOTAL_TRADES_COUNT, WINNING_TRADES, LOSING_TRADES,
                POSITION_ALLOCATION, BEST_PERFORMER_PCT, WORST_PERFORMER_PCT,
                AVG_HOLDING_HOURS, DAILY_PNL_USD,
            )

            # Session return
            if start_equity > 0:
                SESSION_RETURN_PCT.set(
                    round((current_equity - start_equity) / start_equity * 100, 2))

            # Max drawdown tracking
            peak = float(await self.redis.get(REDIS_PEAK_EQUITY) or current_equity)
            if current_equity > peak:
                peak = current_equity
                await self.redis.set(REDIS_PEAK_EQUITY, str(peak))
            dd_pct = (peak - current_equity) / peak * 100 if peak > 0 else 0
            max_dd = float(await self.redis.get(REDIS_MAX_DD) or 0)
            if dd_pct > max_dd:
                max_dd = dd_pct
                await self.redis.set(REDIS_MAX_DD, str(max_dd))
            MAX_DRAWDOWN_PCT.set(round(max_dd, 2))

            # Trade stats
            realized_pnl, wins, losses, total_trades = await self._get_session_stats()
            TOTAL_TRADES_COUNT.set(total_trades)
            WINNING_TRADES.set(wins)
            LOSING_TRADES.set(losses)
            DAILY_PNL_USD.set(realized_pnl)

            # Profit factor
            pf = await self._get_profit_factor()
            PROFIT_FACTOR.set(pf)

            # Position allocation, best/worst, avg holding
            best_pnl_pct = 0.0
            worst_pnl_pct = 0.0
            holding_hours = []
            for p in positions:
                size = float(p.get("size", 0) or 0)
                if size <= 0:
                    continue
                ticker = p.get("ticker", p.get("symbol", ""))
                entry = float(p.get("entry_price", 0) or 0)
                mark = float(p.get("current_price", 0) or entry)
                pnl_pct = ((mark - entry) / entry * 100) if entry > 0 and p.get("side") == "Buy" else (
                    ((entry - mark) / entry * 100) if entry > 0 else 0)
                # Allocation: notional / equity
                notional = entry * abs(size)
                alloc = (notional / current_equity * 100) if current_equity > 0 else 0
                POSITION_ALLOCATION.labels(ticker=ticker).set(round(alloc, 2))
                if pnl_pct > best_pnl_pct:
                    best_pnl_pct = pnl_pct
                if pnl_pct < worst_pnl_pct:
                    worst_pnl_pct = pnl_pct
                # Position age from age_hours if available
                age = float(p.get("age_hours", 0) or 0)
                if age > 0:
                    holding_hours.append(age)

            BEST_PERFORMER_PCT.set(round(best_pnl_pct, 2))
            WORST_PERFORMER_PCT.set(round(worst_pnl_pct, 2))
            if holding_hours:
                AVG_HOLDING_HOURS.set(round(sum(holding_hours) / len(holding_hours), 1))

            # Wallet & ASM dashboard metrics
            from src.metrics.crypto_metrics import (
                update_wallet_metrics, update_asm_uptime, update_asm_next_scan,
            )
            available = current_equity - sum(
                float(p.get("entry_price", 0)) * abs(float(p.get("size", 0)))
                for p in positions if float(p.get("size", 0)) > 0)
            update_wallet_metrics(current_equity, max(0, available), current_equity - available)

            start_ts = float(await self.redis.get(REDIS_START_TIME) or 0)
            if start_ts > 0:
                update_asm_uptime(start_ts)

            config = json.loads(await self.redis.get(REDIS_CONFIG) or "{}")
            interval_sec = config.get("interval_min", DEFAULT_INTERVAL_MIN) * 60
            last_scan = float(await self.redis.get(REDIS_PROGRESS_TS) or 0)
            if last_scan > 0:
                elapsed_since_scan = time.time() - last_scan
                update_asm_next_scan(max(0, interval_sec - elapsed_since_scan))
        except Exception as e:
            logger.debug("asm_metrics_update_failed", error=str(e))

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
            REDIS_ACTIVE, REDIS_PAUSED, REDIS_CONFIG, REDIS_START_TIME,
            REDIS_START_EQUITY, REDIS_PROGRESS_TS,
            REDIS_PEAK_EQUITY, REDIS_MAX_DD,
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

    # ── Dashboard Helpers ────────────────────────────────────────

    async def get_uptime(self) -> str:
        """Get human-readable uptime string (e.g., '04h 12m')."""
        try:
            start_ts = await self.redis.get(REDIS_START_TIME)
            if not start_ts:
                return "N/A"
            elapsed = time.time() - float(start_ts)
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            if hours > 0:
                return f"{hours:02d}h {minutes:02d}m"
            return f"{minutes:02d}m"
        except Exception:
            return "N/A"

    async def get_session_id(self) -> str:
        """Get a short session identifier from DB (e.g., '#A8F3')."""
        try:
            from src.models.database import async_session
            from src.models.tables import CryptoAutoSession
            from sqlalchemy import select, desc
            async with async_session() as session:
                result = await session.execute(
                    select(CryptoAutoSession.id)
                    .order_by(desc(CryptoAutoSession.id))
                    .limit(1)
                )
                row = result.scalar()
                if row:
                    return f"#{row:04X}"
        except Exception:
            pass
        return "N/A"

    async def get_session_pnl(self) -> tuple[float, float]:
        """Get (realized_pnl, unrealized_pnl) for current session."""
        realized = await self._get_session_realized_pnl()
        unrealized = 0.0
        try:
            positions = await self.bybit.get_positions()
            for p in (positions or []):
                size = float(p.get("size", 0) or 0)
                if size <= 0:
                    continue
                # Support both spellings from Bybit (unrealized_pnl) and DB (unrealised_pnl)
                unrealized += float(p.get("unrealized_pnl", 0) or p.get("unrealised_pnl", 0) or 0)
        except Exception:
            pass
        return realized, unrealized

    async def get_last_session_stats(self) -> dict:
        """Get the last completed session's PnL and duration for dashboard display."""
        try:
            from src.models.database import async_session
            from src.models.tables import CryptoAutoSession
            from sqlalchemy import select, desc
            async with async_session() as session:
                result = await session.execute(
                    select(CryptoAutoSession)
                    .where(CryptoAutoSession.status.in_(["STOPPED", "COMPLETED"]))
                    .order_by(desc(CryptoAutoSession.ended_at))
                    .limit(1)
                )
                row = result.scalar()
                if not row:
                    return {}
                pnl = float(row.realized_pnl or 0)
                duration = ""
                if row.started_at and row.ended_at:
                    elapsed = (row.ended_at - row.started_at).total_seconds()
                    h = int(elapsed // 3600)
                    m = int((elapsed % 3600) // 60)
                    duration = f"{h:02d}h {m:02d}m"
                starting = float(row.starting_equity or 1)
                pnl_pct = (pnl / starting * 100) if starting else 0
                return {
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "trades": row.total_trades or 0,
                    "wins": row.wins or 0,
                    "losses": row.losses or 0,
                    "duration": duration,
                    "status": row.status,
                }
        except Exception as e:
            logger.debug("asm_last_session_stats_failed", error=str(e))
            return {}

    async def get_session_history(self, page: int = 0, per_page: int = 5) -> tuple[list[dict], int]:
        """Get paginated session history. Returns (sessions, total_count)."""
        try:
            from src.models.database import async_session
            from src.models.tables import CryptoAutoSession
            from sqlalchemy import select, func, desc
            async with async_session() as session:
                count_result = await session.execute(
                    select(func.count(CryptoAutoSession.id))
                )
                total = count_result.scalar() or 0

                result = await session.execute(
                    select(CryptoAutoSession)
                    .order_by(desc(CryptoAutoSession.started_at))
                    .offset(page * per_page)
                    .limit(per_page)
                )
                rows = result.scalars().all()
                sessions = []
                for r in rows:
                    pnl = float(r.realized_pnl or 0)
                    starting = float(r.starting_equity or 1)
                    pnl_pct = (pnl / starting * 100) if starting else 0
                    duration = ""
                    if r.started_at and r.ended_at:
                        elapsed = (r.ended_at - r.started_at).total_seconds()
                        h = int(elapsed // 3600)
                        m = int((elapsed % 3600) // 60)
                        duration = f"{h:02d}h {m:02d}m"
                    elif r.started_at:
                        elapsed = (datetime.now(timezone.utc) - r.started_at).total_seconds()
                        h = int(elapsed // 3600)
                        m = int((elapsed % 3600) // 60)
                        duration = f"{h:02d}h {m:02d}m"
                    sessions.append({
                        "id": r.id,
                        "id_hex": f"#{r.id:04X}",
                        "status": r.status or "UNKNOWN",
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "trades": r.total_trades or 0,
                        "wins": r.wins or 0,
                        "losses": r.losses or 0,
                        "duration": duration,
                        "started_at": r.started_at,
                        "config": r.config or {},
                    })
                return sessions, total
        except Exception as e:
            logger.debug("asm_session_history_failed", error=str(e))
            return [], 0
