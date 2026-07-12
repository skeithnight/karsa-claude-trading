"""Karsa Trading System - Crypto Telegram Bot Handlers (Simplified)"""

import json
import redis.asyncio as redis
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.config import settings
from src.utils.format import HTML, bold, italic, code, pre, fmt, join
from src.utils.telegram_helpers import send_or_edit_message, send_toast
from src.utils.logging import get_logger

logger = get_logger("crypto_handlers")


def _get_bybit(context: ContextTypes.DEFAULT_TYPE):
    orch = context.bot_data.get("orchestrator")
    if orch:
        return orch.mcp._get_bybit()
    raise RuntimeError("Orchestrator not connected — cannot access BybitClient")

def _get_redis(context: ContextTypes.DEFAULT_TYPE):
    client = context.bot_data.get("redis_client")
    if client:
        return client
    # Finding 10: fallback creates a new Redis client per call — log warning
    import logging
    logging.getLogger("crypto_handlers").warning(
        "redis_fallback_client_created — bot_data[redis_client] is None"
    )
    return redis.from_url(settings.REDIS_URL, decode_responses=True)

def _is_authorized(update: Update) -> bool:
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if not settings.TELEGRAM_CHAT_ID:
        return False
    if chat_id != str(settings.TELEGRAM_CHAT_ID):
        return False
    return True

async def _reply(update: Update, content, **kwargs):
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    content = fmt(italic(ts), "\n", content)
    if isinstance(content, HTML) and "parse_mode" not in kwargs:
        kwargs["parse_mode"] = "HTML"
    text = str(content)
    if update.callback_query:
        try:
            return await update.callback_query.message.edit_text(text, **kwargs)
        except Exception:
            return await update.callback_query.message.reply_text(text, **kwargs)
    elif update.message:
        return await update.message.reply_text(text, **kwargs)
    return None

def build_main_keyboard():
    """Unified navigation keyboard — consistent across all views."""
    keyboard = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="cmd_dashboard"),
         InlineKeyboardButton("📋 Activity", callback_data="cmd_activity")],
        [InlineKeyboardButton("💼 Portfolio", callback_data="cmd_portfolio"),
         InlineKeyboardButton("📈 Performance", callback_data="cmd_performance")],
        [InlineKeyboardButton("📡 Universe", callback_data="universe_detail"),
         InlineKeyboardButton("🎛️ Control", callback_data="cmd_control")],
    ]
    return InlineKeyboardMarkup(keyboard)

# --- 1. Unified ASM Dashboard (The Main Hub) ---

async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unified ASM Dashboard — adapts layout based on ASM state (IDLE vs ACTIVE)."""
    if not _is_authorized(update): return

    import asyncio
    import time
    t0 = time.monotonic()
    r = _get_redis(context)
    orch = context.bot_data.get("orchestrator")
    bybit = _get_bybit(context)

    # --- Parallel fetch all data with timing ---
    async def _fetch_redis():
        t = time.monotonic()
        try:
            redis_ok = await r.ping()
            halt_active = bool(await r.get("karsa:global_halt"))
            is_active = (await r.get("karsa:auto:state:active")) == "1"
            logger.info("fetch_redis_done", ms=int((time.monotonic()-t)*1000))
            return {"redis_ok": redis_ok, "halt_active": halt_active, "is_active": is_active}
        except Exception:
            return {"redis_ok": False, "halt_active": False, "is_active": False}

    async def _fetch_db():
        t = time.monotonic()
        try:
            from src.models.database import async_session
            from sqlalchemy import text
            async with async_session() as session:
                await session.execute(text("SELECT 1"))
            logger.info("fetch_db_done", ms=int((time.monotonic()-t)*1000))
            return True
        except Exception:
            return False

    async def _fetch_wallet():
        t = time.monotonic()
        try:
            wallet = await bybit.get_wallet_balance()
            logger.info("fetch_wallet_done", ms=int((time.monotonic()-t)*1000))
            return {"wallet": wallet, "ok": not wallet.get("error")}
        except Exception:
            return {"wallet": {}, "ok": False}

    async def _fetch_regime():
        t = time.monotonic()
        try:
            from src.advisory.crypto_regime import CryptoRegimeFilter
            regime = await CryptoRegimeFilter(orch.mcp).get_current_regime()
            logger.info("fetch_regime_done", ms=int((time.monotonic()-t)*1000))
            return {
                "state": regime.get("state", "UNKNOWN"),
                "hurst": regime.get("hurst", 0.5),
                "adx": regime.get("adx", 0.0),
            }
        except Exception:
            return {"state": "UNKNOWN", "hurst": 0.5, "adx": 0.0}

    async def _fetch_movers():
        t = time.monotonic()
        try:
            from src.advisory.crypto_market_watch import CryptoMarketWatchEngine
            movers = await CryptoMarketWatchEngine.get_top_movers(orch.mcp)
            logger.info("fetch_movers_done", ms=int((time.monotonic()-t)*1000))
            if movers:
                best = movers[0]
                return f" • Top: {best.get('symbol', '?')} ({best.get('change_pct', 0):+.1f}%)"
        except Exception:
            pass
        return ""

    async def _fetch_profile():
        t = time.monotonic()
        try:
            if orch and orch.profile_manager:
                p = await orch.profile_manager.get_active_profile()
                logger.info("fetch_profile_done", ms=int((time.monotonic()-t)*1000))
                return f"{p.emoji} {p.name.upper().replace('_', ' ')}"
        except Exception:
            pass
        return ""

    # Execute all fetches in parallel with individual timeouts
    async def _with_timeout(coro, timeout_sec):
        try:
            return await asyncio.wait_for(coro, timeout=timeout_sec)
        except (asyncio.TimeoutError, Exception):
            return None

    results = await asyncio.gather(
        _with_timeout(_fetch_redis(), 2),
        _with_timeout(_fetch_db(), 2),
        _with_timeout(_fetch_wallet(), 3),
        _with_timeout(_fetch_regime(), 5),
        _with_timeout(_fetch_movers(), 5),
        _with_timeout(_fetch_profile(), 2),
    )

    logger.info("dashboard_parallel_fetch_total", ms=int((time.monotonic()-t0)*1000))

    redis_data = results[0] if isinstance(results[0], dict) else {"redis_ok": False, "halt_active": False, "is_active": False}
    db_ok = results[1] if isinstance(results[1], bool) else False
    wallet_data = results[2] if isinstance(results[2], dict) else {"wallet": {}, "ok": False}
    regime_data = results[3] if isinstance(results[3], dict) else {"state": "UNKNOWN", "hurst": 0.5, "adx": 0.0}
    top_mover_str = results[4] if isinstance(results[4], str) else ""
    profile_str = results[5] if isinstance(results[5], str) else ""

    redis_ok = redis_data.get("redis_ok", False)
    halt_active = redis_data.get("halt_active", False)
    is_active = redis_data.get("is_active", False)
    bybit_ok = wallet_data.get("ok", False)
    wallet = wallet_data.get("wallet", {})
    regime_state = regime_data.get("state", "UNKNOWN")

    system_online = all([redis_ok, bybit_ok, db_ok])
    sys_icon = "🟢" if system_online else "🔴"
    asm_icon = "🟢" if is_active else "🔴"
    regime_icon = "🟢" if "BULL" in regime_state else "🔴" if "BEAR" in regime_state else "🟡"
    halt_line = f"\n🚨 HALT ACTIVE" if halt_active else ""

    balance = wallet.get("balance", 0)
    available = wallet.get("available", 0)
    margin = wallet.get("used_margin", 0)
    avail_pct = (available / max(balance, 1)) * 100

    if is_active:
        # --- ACTIVE STATE ---
        from src.agents.autonomous_session import AutonomousSessionManager
        asm = AutonomousSessionManager(orch, r, bybit)
        uptime = await asm.get_uptime()
        session_id = await asm.get_session_id()
        realized, unrealized = await asm.get_session_pnl()
        total_pnl = realized + unrealized
        pnl_icon = "🟢" if total_pnl >= 0 else "🔴"

        open_count = 0
        try:
            positions = await bybit.get_positions()
            open_count = sum(1 for p in positions if float(p.get("size", 0)) > 0)
        except Exception: pass

        # Get starting equity from session config
        starting_equity = balance  # default to current balance
        try:
            config = json.loads(await r.get("karsa:auto:config") or "{}")
            starting_equity = float(config.get("starting_equity", balance) or balance)
        except Exception:
            pass
        current_equity = starting_equity + total_pnl

        # Next scan estimate
        next_scan_str = "..."
        try:
            start_ts = float(await r.get("karsa:auto:start_time") or 0)
            config = json.loads(await r.get("karsa:auto:config") or "{}")
            interval_min = config.get("interval_min", 15)
            if start_ts > 0:
                import time
                elapsed = time.time() - start_ts
                remaining = interval_min * 60 - (elapsed % (interval_min * 60))
                m, s = int(remaining // 60), int(remaining % 60)
                next_scan_str = f"{m:02d}m {s:02d}s"
        except Exception: pass

        text = fmt(
            bold("🟢 ACTIVE SESSION 🟢"), "\n",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
            f"🆔 Session ID: {session_id}\n",
            f"⏱ Uptime: {uptime} | Next Scan: {next_scan_str}\n",
            f"💰 Starting Equity: ${starting_equity:,.2f}\n",
            f"📈 Current Equity: ${current_equity:,.2f}\n\n",
            bold("Performance:"), "\n",
            f"Realized PnL: {realized:+,.2f} USD {'🟢' if realized >= 0 else '🔴'}\n",
            f"Unrealized PnL: {unrealized:+,.2f} USD {'🟢' if unrealized >= 0 else '🔴'}\n",
            f"Total PnL: {total_pnl:+,.2f} USD ({(total_pnl/starting_equity*100) if starting_equity else 0:+.2f}%) {pnl_icon}\n\n",
            f"📂 Open Positions: {open_count}",
            halt_line, "\n",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
        )
        keyboard = [
            [InlineKeyboardButton("📊 View Positions", callback_data="view_positions_detail"),
             InlineKeyboardButton("🔄 Refresh", callback_data="cmd_dashboard")],
            [InlineKeyboardButton("📜 Trade History", callback_data="cmd_trade_history")],
            [InlineKeyboardButton("⏸ Pause Session", callback_data="auto_pause"),
             InlineKeyboardButton("🛑 Stop & Close All", callback_data="auto_stop")],
        ]
    else:
        # --- IDLE STATE ---
        last_run_str = ""
        from src.agents.autonomous_session import AutonomousSessionManager
        asm = AutonomousSessionManager(orch, r, bybit)
        last_stats = await asm.get_last_session_stats()
        if last_stats:
            pnl = last_stats.get("pnl", 0)
            pnl_icon = "🟢" if pnl >= 0 else "🔴"
            ago = last_stats.get("duration", "")
            last_run_str = f"Last Run: {pnl_icon} ${pnl:+,.2f} ({last_stats.get('pnl_pct', 0):+.1f}%) • {ago}"

        text = fmt(
            bold("🤖 Autonomous Session Manager"), "\n",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
            f"{sys_icon} System Status: {'Healthy' if system_online else 'Degraded'}", halt_line, "\n",
            f"💰 Wallet Balance: ${balance:,.2f}\n",
            f"📊 Market Regime: {regime_icon} {regime_state}", top_mover_str, "\n",
            f"📉 Last Trade PnL: {last_run_str}\n",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
            "Ready to deploy capital. Select an action below.",
        )
        keyboard = [
            [InlineKeyboardButton("🚀 LAUNCH NEW SESSION", callback_data="auto_launch")],
            [InlineKeyboardButton("📜 Trade History", callback_data="cmd_trade_history"),
             InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings")],
        ]

    await send_or_edit_message(update, text, reply_markup=InlineKeyboardMarkup(keyboard))

# --- 1b. Session History (Slide-up View) ---

async def session_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paginated session history. Shows past ASM sessions with PnL."""
    if not _is_authorized(update): return

    orch = context.bot_data.get("orchestrator")
    r = _get_redis(context)
    bybit = _get_bybit(context)

    page = 0
    if update.callback_query and update.callback_query.data.startswith("cmd_history_p"):
        try:
            page = int(update.callback_query.data.split("_")[-1])
        except (ValueError, IndexError):
            page = 0

    from src.agents.autonomous_session import AutonomousSessionManager
    asm = AutonomousSessionManager(orch, r, bybit)
    sessions, total = await asm.get_session_history(page=page)
    total_pages = max(1, (total + 4) // 5)

    # Aggregate net PnL across page sessions (Gap 3)
    total_net_pnl = sum(s["pnl"] for s in sessions) if sessions else 0.0
    net_pnl_icon = "🟢" if total_net_pnl >= 0 else "🔴"
    pnl_str = f" • Net PnL: {net_pnl_icon} {total_net_pnl:+,.2f} USD" if sessions else ""

    lines = [
        bold("📂 SESSION HISTORY"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
        f"Total Sessions: {total}{pnl_str}", "\n\n",
    ]

    if not sessions:
        lines.append("No sessions recorded yet.")
    else:
        for s in sessions:
            status = s["status"]
            icon = "🟢" if status == "COMPLETED" else "🔴" if status == "STOPPED" else "🔵"
            pnl = s["pnl"]
            pnl_icon = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"{icon} {s['id_hex']} ({status})")
            lines.append(f"PnL: {pnl_icon} {pnl:+,.2f} USD ({s['pnl_pct']:+.1f}%) • {s['duration']}")
            lines.append("")

    nav = ""
    if total_pages > 1:
        nav = f"Page {page + 1}/{total_pages}"
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Build keyboard: navigation only (per UI feedback)
    keyboard = []

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"cmd_history_p{page - 1}"))
    if nav:
        nav_row.append(InlineKeyboardButton(nav, callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"cmd_history_p{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("🏠 Back to Dashboard", callback_data="cmd_dashboard")])

    await _reply(update, fmt(*lines, sep="\n"), reply_markup=InlineKeyboardMarkup(keyboard))


# --- 1b-detail. Session Detail (drill-down) ---

async def session_detail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Drill-down detail view for a single ASM session."""
    if not _is_authorized(update): return

    data = update.callback_query.data if update.callback_query else ""
    try:
        session_id = int(data.replace("session_detail_", ""))
    except (ValueError, AttributeError):
        await _reply(update, "❌ Invalid session ID.")
        return

    try:
        from src.models.database import async_session
        from src.models.tables import CryptoAutoSession
        from sqlalchemy import select
        async with async_session() as db:
            res = await db.execute(select(CryptoAutoSession).where(CryptoAutoSession.id == session_id))
            row = res.scalar_one_or_none()
    except Exception as e:
        logger.error("session_detail_db_failed", error=str(e))
        row = None

    if not row:
        keyboard = [[InlineKeyboardButton("⬅️ Back to History", callback_data="cmd_history")]]
        await _reply(update, "❌ Session not found.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    pnl = float(row.realized_pnl or 0)
    pnl_icon = "🟢" if pnl >= 0 else "🔴"
    starting = float(row.starting_equity or 1)
    pnl_pct = (pnl / starting * 100) if starting else 0
    total_trades = row.total_trades or 0
    wins = row.wins or 0
    losses = row.losses or 0
    win_rate = (wins / max(total_trades, 1)) * 100 if total_trades else 0
    duration = "N/A"
    if row.started_at and row.ended_at:
        elapsed = (row.ended_at - row.started_at).total_seconds()
        duration = f"{int(elapsed // 3600):02d}h {int((elapsed % 3600) // 60):02d}m"
    elif row.started_at:
        from datetime import timezone
        from datetime import datetime as _dt
        elapsed = (_dt.now(timezone.utc) - row.started_at).total_seconds()
        duration = f"{int(elapsed // 3600):02d}h {int((elapsed % 3600) // 60):02d}m (live)"
    cfg = row.config or {}
    started_str = row.started_at.strftime("%Y-%m-%d %H:%M") if row.started_at else "N/A"
    status_icon = "🟢" if row.status == "COMPLETED" else "🔴" if row.status == "STOPPED" else "🔵"

    text = fmt(
        bold(f"📊 SESSION #{row.id:04X} DETAIL"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
        f"{status_icon} Status: {row.status}\n",
        f"Started: {started_str}\n",
        f"Duration: {duration}\n\n",
        bold("💰 Performance"), "\n",
        f"PnL: {pnl_icon} {pnl:+,.2f} USD ({pnl_pct:+.2f}%)\n",
        f"Trades: {total_trades} | W: {wins} | L: {losses} | WR: {win_rate:.0f}%\n\n",
        bold("⚙️ Session Config"), "\n",
        f"Risk: {cfg.get('risk_pct', 'N/A')}% | Max Pos: {cfg.get('max_pos', 'N/A')}\n",
        f"Interval: {cfg.get('interval_min', 'N/A')}m | "
        f"Duration: {str(cfg.get('duration_min', 0) or '∞')}m\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    )

    keyboard = []
    if row.status in ["STOPPED", "COMPLETED", "CRASHED"]:
        keyboard.append([InlineKeyboardButton("🔄 RERUN THIS CONFIG", callback_data=f"rerun_cfg_{row.id}")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to History", callback_data="cmd_history")])
    keyboard.append([InlineKeyboardButton("🏠 Back to Dashboard", callback_data="cmd_dashboard")])

    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))

# --- 1c. Manage Profiles ---

async def manage_profiles_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show risk profile selection with params."""
    if not _is_authorized(update): return

    orch = context.bot_data.get("orchestrator")
    if not orch or not orch.profile_manager:
        await _reply(update, "⚠️ Profile manager not initialized.",
                     reply_markup=InlineKeyboardMarkup(
                         [[InlineKeyboardButton("🏠 Back to Dashboard", callback_data="cmd_dashboard")]]))
        return

    active = await orch.profile_manager.get_active_profile()

    profiles = [
        ("🛡️ CONSERVATIVE", "conservative", "Max 1% Risk • Max 2 Pos • 1.0x ATR SL"),
        ("⚖️ BALANCED", "semi_aggressive", "Max 3% Risk • Max 5 Pos • 1.5x ATR SL"),
        ("🔥 AGGRESSIVE", "aggressive", "Max 5% Risk • Max 8 Pos • 2.0x ATR SL"),
    ]

    lines = [
        bold("⚙️ MANAGE PROFILES"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
        "Select the risk doctrine for the ASM.\n",
    ]

    keyboard = []
    for emoji_name, name, desc in profiles:
        is_current = active and active.name == name
        status = " (ACTIVE)" if is_current else ""
        lines.append(f"{emoji_name}{status}\n{desc}\n")
        
        btn_action = "✅ ACTIVE" if is_current else "Select"
        keyboard.append([
            InlineKeyboardButton(btn_action, callback_data=f"profile_{name}"),
            InlineKeyboardButton("Edit", callback_data=f"edit_profile_{name}")
        ])

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    keyboard.append([InlineKeyboardButton("🌐 Manage Universe Scope", callback_data="universe_detail")])
    keyboard.append([InlineKeyboardButton("🏠 Back to Dashboard", callback_data="cmd_dashboard")])

    await _reply(update, fmt(*lines, sep="\n"), reply_markup=InlineKeyboardMarkup(keyboard))

# --- 1d. Open Positions ---

async def open_positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Live view of current open Bybit positions."""
    if not _is_authorized(update): return

    bybit = _get_bybit(context)
    positions = []
    try:
        raw = await bybit.get_positions()
        positions = [p for p in raw if float(p.get("size", 0)) > 0]
    except Exception: pass

    lines = [
        bold("💼 OPEN POSITIONS (Live)"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
    ]

    if not positions:
        lines.append("No open positions.")
    else:
        for p in positions:
            ticker = p.get("symbol", "?")
            side = p.get("side", "?")
            size = float(p.get("size", 0))
            entry = float(p.get("entry_price", 0) or 0)
            mark = float(p.get("current_price", 0) or 0)
            uPnL = float(p.get("unrealised_pnl", 0))
            pnl_icon = "🟢" if uPnL >= 0 else "🔴"
            pnl_pct = ((mark - entry) / entry * 100) if side == "Buy" and entry > 0 else (
                ((entry - mark) / entry * 100) if entry > 0 else 0)
            side_icon = "⬆️" if side == "Buy" else "⬇️"
            lines.append(f"{side_icon} {ticker} • {side} • {size}")
            lines.append(f"Entry: ${entry:,.2f} → Mark: ${mark:,.2f}")
            lines.append(f"PnL: {pnl_icon} ${uPnL:+,.2f} ({pnl_pct:+.2f}%)")
            lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    keyboard = [[InlineKeyboardButton("🏠 Back to Dashboard", callback_data="cmd_dashboard")]]

    await _reply(update, fmt(*lines, sep="\n"), reply_markup=InlineKeyboardMarkup(keyboard))

async def activity_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update): return
    try:
        from src.models.database import async_session
        from src.models.tables import Signal, ClosedPaperTrade, CryptoPosition
        from sqlalchemy import select, desc
        
        async with async_session() as session:
            sig_result = await session.execute(
                select(Signal).where(Signal.market == "CRYPTO")
                .order_by(desc(Signal.created_at)).limit(30)
            )
            all_signals = sig_result.scalars().all()
            
            # Deduplicate signals by ticker to show diverse activity
            seen_tickers = set()
            signals = []
            for s in all_signals:
                if s.ticker not in seen_tickers:
                    seen_tickers.add(s.ticker)
                    signals.append(s)
                if len(signals) >= 5:
                    break

            trade_result = await session.execute(
                select(ClosedPaperTrade).where(ClosedPaperTrade.market == "CRYPTO")
                .order_by(desc(ClosedPaperTrade.exit_date)).limit(10)
            )
            all_trades = trade_result.scalars().all()
            
            # Deduplicate trades by ticker
            seen_trade_tickers = set()
            trades = []
            for t in all_trades:
                if t.ticker not in seen_trade_tickers:
                    seen_trade_tickers.add(t.ticker)
                    trades.append(t)
                if len(trades) >= 3:
                    break
            
        lines = [bold("📋 LIVE ACTIVITY FEED"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"]
        
        feed = []
        for s in signals:
            ts = s.created_at.strftime("%H:%M") if s.created_at else "?"
            emoji = "✅" if s.status == "EXECUTED" else "⛔" if s.status == "REJECTED" else "🔍"
            side = "🟢 LONG" if s.direction == "LONG" else "🔴 SHORT"
            text_block = fmt(code(ts), " ", emoji, " ", bold(s.status), " ", s.ticker, " ", side, "\nConf: ", str(s.confidence_score), "%")
            if s.status == "EXECUTED": text_block = fmt(text_block, " | Fill: $", f"{s.entry_price:,.2f}")
            full_r = s.reasoning or "No thesis."
            text_block = fmt(text_block, "\n", italic(f"Thesis: {full_r[:75]}..."))
            feed.append((s.created_at, text_block))
            
        for t in trades:
            ts = t.exit_date.strftime("%H:%M") if t.exit_date else "?"
            emoji = "🟢" if (t.realized_pnl_pct or 0) > 0 else "🔴"
            text_block = fmt(code(ts), " 💰 ", bold("CLOSED"), " ", t.ticker, " ", t.side, "\nPnL: ", emoji, " ", f"{t.realized_pnl_pct:+.2f}% | ", italic(f"Reason: {t.exit_reason}"))
            feed.append((t.exit_date, text_block))
            
        feed.sort(key=lambda x: x[0], reverse=True)
        
        for _, block in feed[:7]:
            lines.append(fmt(block, "\n\n"))
            
        if not feed: lines.append("📭 No recent activity.")
            
        # Gap 5: slide-up views use single Back to Dashboard, not the full nav grid
        back_keyboard = [[InlineKeyboardButton("🏠 Back to Dashboard", callback_data="cmd_dashboard")]]
        await _reply(update, fmt(*lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back_keyboard))
    except Exception as e:
        logger.error("activity_failed", error=str(e))
        await _reply(update, "❌ Activity load failed.", reply_markup=build_main_keyboard())

# --- 3. Portfolio (Positions & PnL) ---

async def portfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update): return
    try:
        from src.utils.telegram_helpers import format_pre_table
        bybit = _get_bybit(context)
        positions = await bybit.get_positions()
        
        if not positions:
            text = fmt(bold("💼 ACTIVE PORTFOLIO"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n", italic("📭 No active positions. Desk is in cash."))
            await _reply(update, text, reply_markup=build_main_keyboard())
            return

        headers = ["Sym", "Side", "Size", "Mark", "uPnL"]
        rows = []
        total_pnl = 0.0
        
        for p in positions:
            pnl = p.get("unrealized_pnl", 0.0)
            total_pnl += pnl
            emoji = "🟢" if pnl >= 0 else "🔴"
            side = "L" if p.get("side") == "Buy" else "S"
            rows.append([
                p.get("ticker", "?")[:6], side, f"{p.get('size', 0):.3f}",
                f"${p.get('current_price', 0):,.2f}", f"{emoji}${pnl:+,.1f}"
            ])
            
        table = format_pre_table(headers, rows, align_right=[2, 3, 4])
        t_emoji = "🟢" if total_pnl >= 0 else "🔴"
        
        text = fmt(
            bold("💼 ACTIVE PORTFOLIO"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
            pre(table), "\n",
            bold(f"Total Unrealized: {t_emoji} ${total_pnl:+,.2f}")
        )
        await _reply(update, text, reply_markup=build_main_keyboard())
    except Exception as e:
        logger.error("portfolio_failed", error=str(e))
        await _reply(update, "❌ Portfolio load failed.", reply_markup=build_main_keyboard())

# --- 4. Performance (Analytics & Audit) ---

async def performance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update): return
    try:
        from src.advisory.performance_tracker import PerformanceTracker
        from src.advisory.crypto_audit import CryptoAuditMetrics
        from src.agents.crypto_auditor import CryptoAuditorAgent
        
        msg = await _reply(update, "📈 Compiling performance and AI audit...")
        
        tracker = PerformanceTracker()
        stats = await tracker.get_cumulative_stats(days=30)
        dd_data = await tracker.get_max_drawdown(days=30)
        
        engine = CryptoAuditMetrics()
        metrics = await engine.gather(days=30)
        
        pnl = stats.get('total_realized_pnl', 0)
        pnl_e = "🟢" if pnl >= 0 else "🔴"
        win_rate = metrics.get('win_rate', 0)
        total_trades = stats.get('trade_count', 0)
        pf = metrics.get('profit_factor', float('inf'))
        dd = dd_data.get('max_drawdown_pct', 0)
        
        perf_block = (
            f"30-Day PnL   : {pnl_e} ${pnl:+,.2f}\n"
            f"Win Rate     : {win_rate}%\n"
            f"Total Trades : {total_trades}\n"
            f"Profit Factor: {pf:.2f}\n"
            f"Max Drawdown : {dd:.1f}%"
        )
        
        audit_block = "Insufficient data for LLM audit rating."
        if total_trades >= 2:
            try:
                orch = context.bot_data.get("orchestrator")
                auditor = CryptoAuditorAgent(orch.mcp)
                analysis = await auditor.run_audit(metrics)
                grade = analysis.get("grade", "?")
                summary = str(analysis.get("summary", "No summary"))[:150]
                audit_block = f"Grade: [{grade}]\nNote: {summary}"
            except Exception: pass
            
        text = fmt(
            bold("📈 PERFORMANCE & AUDIT"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
            bold("Key Metrics (30D):"), "\n", pre(perf_block), "\n\n",
            bold("AI Self-Audit:"), "\n", pre(audit_block)
        )
        await msg.edit_text(str(text), parse_mode="HTML", reply_markup=build_main_keyboard())
    except Exception as e:
        logger.error("performance_failed", error=str(e))
        await _reply(update, "❌ Performance load failed.", reply_markup=build_main_keyboard())

# --- 4b. Settings (Preferences & Toggles) ---

async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot settings and preferences with inline toggles."""
    if not _is_authorized(update): return

    r = _get_redis(context)

    # Read current settings from Redis
    try:
        alerts_raw = await r.get("karsa:alerts_enabled")
        alerts_on = alerts_raw in ("1", b"1") if alerts_raw is not None else True
    except Exception:
        alerts_on = True

    try:
        max_pos = await r.get("karsa:settings:max_positions") or "5"
    except Exception:
        max_pos = "5"

    try:
        regime_raw = await r.get("karsa:settings:regime_filter")
        regime_on = regime_raw in ("1", b"1") if regime_raw is not None else True
    except Exception:
        regime_on = True

    try:
        profile_raw = await r.get("karsa:state:risk_profile")
        profile_name = profile_raw.decode() if isinstance(profile_raw, bytes) else (profile_raw or "conservative")
    except Exception:
        profile_name = "conservative"

    text = fmt(
        bold("⚙️ Bot Settings & Preferences"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n\n",
        bold("Current Configuration:"), "\n\n",
        f"📂 Max Open Positions: {max_pos}\n",
        f"📊 Regime Filter: {'ENABLED' if regime_on else 'DISABLED'} (Only trade in BULL/NEUTRAL)\n",
        f"🔔 Trade Alerts: {'ENABLED' if alerts_on else 'MUTED'} (SL/TP notifications)\n",
        f"🛡️ Risk Profile: {profile_name.upper()}\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
        "Select a parameter below to modify it.",
    )

    keyboard = [
        [InlineKeyboardButton(f"📂 Max Pos: {max_pos}", callback_data="toggle_max_pos"),
         InlineKeyboardButton(f"📊 Regime: {'ON' if regime_on else 'OFF'}", callback_data="toggle_regime")],
        [InlineKeyboardButton(f"🔔 Alerts: {'ON' if alerts_on else 'OFF'}", callback_data="toggle_alerts"),
         InlineKeyboardButton(f"🛡️ Profile: {profile_name.upper()}", callback_data="toggle_risk_profile")],
        [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="cmd_dashboard")],
    ]

    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def _toggle_max_pos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cycle max positions: 3 → 5 → 8 → 3."""
    r = _get_redis(context)
    try:
        current = int(await r.get("karsa:settings:max_positions") or 5)
    except Exception:
        current = 5

    cycle = {3: 5, 5: 8, 8: 3}
    new_val = cycle.get(current, 5)
    await r.set("karsa:settings:max_positions", str(new_val))

    await settings_cmd(update, context)


async def _toggle_regime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle regime filter on/off."""
    r = _get_redis(context)
    try:
        current = await r.get("karsa:settings:regime_filter")
        is_on = current in ("1", b"1") if current is not None else True
    except Exception:
        is_on = True

    await r.set("karsa:settings:regime_filter", "0" if is_on else "1")
    await settings_cmd(update, context)


async def _toggle_risk_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cycle risk profile: conservative → semi_aggressive → aggressive → conservative."""
    orch = context.bot_data.get("orchestrator")
    if not orch or not orch.profile_manager:
        await _reply(update, "⚠️ Profile manager not initialized.")
        return

    cycle = ["conservative", "semi_aggressive", "aggressive"]
    r = _get_redis(context)
    try:
        current_raw = await r.get("karsa:state:risk_profile")
        current = current_raw.decode() if isinstance(current_raw, bytes) else (current_raw or "conservative")
    except Exception:
        current = "conservative"

    next_idx = (cycle.index(current) + 1) % len(cycle) if current in cycle else 0
    new_profile = cycle[next_idx]

    from src.risk.profile_manager import RiskProfile
    ok = await orch.profile_manager.set_profile(RiskProfile(new_profile), f"tg_{update.effective_user.id}", "Settings menu toggle")
    if not ok:
        await _reply(update, "⏳ Cooldown active — wait 5 minutes between profile changes.")
        return

    await settings_cmd(update, context)


# --- 5. Control (Emergency & Overrides) ---

async def control_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update): return

    orch = context.bot_data.get("orchestrator")

    try:
        r = _get_redis(context)
        halt_active = bool(await r.get("karsa:global_halt"))
        cooldown = await r.get("karsa:crypto_cooldown")
    except Exception:
        halt_active, cooldown = False, None

    # Alerts state
    try:
        alerts_raw = await r.get("karsa:alerts_enabled")
        alerts_on = alerts_raw in ("1", b"1") if alerts_raw is not None else True
    except Exception:
        alerts_on = True

    state_block = (
        f"Global Halt: {'🚨 ACTIVE' if halt_active else '🟢 INACTIVE'}\n"
        f"Cooldown: {'⏳ ACTIVE' if cooldown else '🟢 INACTIVE'}\n"
        f"Trade Alerts: {'🔔 ON' if alerts_on else '🔕 MUTED'}"
    )

    # Risk profile block
    profile_block = "Not initialized"
    profile_name = "unknown"
    try:
        if orch and orch.profile_manager:
            p = await orch.profile_manager.get_active_profile()
            profile_name = p.name
            profile_block = (
                f"{p.emoji} {p.name.upper().replace('_', ' ')}\n"
                f"Min Conf: {p.min_confidence}% | Max Pos: {p.max_position_size_pct:.2%}\n"
                f"SL: {p.stop_loss_atr_mult}x ATR | TP: {p.take_profit_atr_mult}x ATR\n"
                f"Max Open: {p.max_open_positions} | Max Daily: {p.max_daily_trades}\n"
                f"Min Vol: ${p.min_volume_24h_usd:,.0f}"
            )
    except Exception: pass

    # Universe block
    universe_block = "Not initialized"
    try:
        if orch and orch.universe_engine:
            universe = await orch.universe_engine.get_current()
            universe_block = f"{len(universe)} coins: {', '.join(universe)}"
    except Exception: pass

    text = fmt(
        bold("🎛️ DESK CONTROL PANEL"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
        bold("System State:"), "\n", pre(state_block), "\n",
        bold("Risk Profile:"), "\n", pre(profile_block), "\n",
        bold("Universe:"), "\n", pre(universe_block), "\n\n",
        italic("Select an operation below.")
    )

    keyboard = [
        [
            InlineKeyboardButton("🛡️ Conservative", callback_data="mode_conservative"),
            InlineKeyboardButton("⚖️ Semi-Agg", callback_data="mode_semi_aggressive"),
            InlineKeyboardButton("🔥 Aggressive", callback_data="mode_aggressive"),
        ],
        [InlineKeyboardButton("🔄 Refresh Universe", callback_data="universe_refresh")],
        [InlineKeyboardButton(
            "🔕 Mute Alerts" if alerts_on else "🔔 Unmute Alerts",
            callback_data="toggle_alerts"
        )],
        [InlineKeyboardButton("🚨 EXECUTE KILL (Close All)", callback_data="crypto_kill")],
        [InlineKeyboardButton("🧹 Sell All (15m break)", callback_data="crypto_sellall")],
        [InlineKeyboardButton("▶️ Resume Operations", callback_data="crypto_resume")],
        [InlineKeyboardButton("🔬 Run Walk-Forward Tests", callback_data="crypto_walkforward")],
        [InlineKeyboardButton("🏠 Dashboard", callback_data="cmd_dashboard")]
    ]
    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))


# --- Action Executors for Control Panel ---

async def _execute_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    operator = update.effective_user.username or str(update.effective_user.id)
    try:
        from src.risk.emergency import activate_global_halt
        from src.risk.sor import SmartOrderRouter
        bybit = _get_bybit(context)
        sor = SmartOrderRouter(bybit)
        await sor.flatten_all()
        await activate_global_halt(reason=f"Manual kill by {operator}", operator=operator)
        await _reply(update, "🚨 EMERGENCY KILL EXECUTED. Global halt active.", reply_markup=build_main_keyboard())
    except Exception:
        await _reply(update, "❌ Kill failed.", reply_markup=build_main_keyboard())

async def _execute_sellall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        from src.risk.sor import SmartOrderRouter
        bybit = _get_bybit(context)
        sor = SmartOrderRouter(bybit)
        await sor.flatten_all()
        r = _get_redis(context)
        await r.set("karsa:crypto_cooldown", "1", ex=900)
        await _reply(update, "🧹 SELL ALL EXECUTED. 15 minute cooldown active.", reply_markup=build_main_keyboard())
    except Exception:
        await _reply(update, "❌ Sell all failed.", reply_markup=build_main_keyboard())

async def _execute_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    operator = update.effective_user.username or str(update.effective_user.id)
    try:
        from src.risk.emergency import deactivate_global_halt
        await deactivate_global_halt(operator=operator)
        r = _get_redis(context)
        await r.delete("karsa:crypto_cooldown")
        await _reply(update, "▶️ TRADING RESUMED. Halts and cooldowns cleared.", reply_markup=build_main_keyboard())
    except Exception:
        await _reply(update, "❌ Resume failed.", reply_markup=build_main_keyboard())

async def walkforward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run realistic backtest bridging AI signals + market data with txn costs."""
    ticker = "BTCUSDT"  # Simple default for the control panel trigger
    try:
        from src.models.database import async_session
        from src.backtest.engine import RealisticCryptoBacktester
        msg = await _reply(update, f"🔬 Running Walk-Forward for {ticker}...")
        tester = RealisticCryptoBacktester(slippage_pct=0.05, taker_fee_pct=0.055)
        async with async_session() as session:
            result = await tester.run(session, ticker, "CRYPTO", days=30, timeframe="4h")
        
        stats = f"Win Rate: {result.win_rate:.1f}% | Sharpe: {result.sharpe_ratio:.2f}\nRet: {result.total_return_pct:+.2f}% | Max DD: {result.max_drawdown_pct:.1f}%"
        await msg.edit_text(str(fmt(bold(f"🔬 WALK-FORWARD: {ticker}"), "\n", pre(stats))), parse_mode="HTML", reply_markup=build_main_keyboard())
    except Exception:
        await _reply(update, "❌ Walk-Forward simulation failed.", reply_markup=build_main_keyboard())


# --- Risk Profile Commands ---

async def mode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current risk profile with inline switching keyboard."""
    if not _is_authorized(update): return
    orch = context.bot_data.get("orchestrator")
    if not orch or not orch.profile_manager:
        await _reply(update, "⚠️ Profile manager not initialized.")
        return

    p = await orch.profile_manager.get_active_profile()
    lines = [
        bold(f"{p.emoji} Current Risk Profile"),
        "",
        bold("Mode: ") + p.name.upper().replace("_", " "),
        "",
        bold("Parameters:"),
        f"├ Min Confidence: {p.min_confidence}%",
        f"├ Max Position Size: {p.max_position_size_pct:.2%}",
        f"├ Stop Loss: {p.stop_loss_atr_mult}x ATR",
        f"├ Take Profit: {p.take_profit_atr_mult}x ATR",
        f"├ Max Open Positions: {p.max_open_positions}",
        f"├ Max Daily Trades: {p.max_daily_trades}",
        f"└ Min 24h Volume: ${p.min_volume_24h_usd:,.0f}",
    ]

    # Universe info
    try:
        if orch.universe_engine:
            universe = await orch.universe_engine.get_current()
            lines.append("")
            lines.append(bold("📡 Universe"))
            lines.append(f"  {len(universe)} coins: {', '.join(universe[:8])}{'...' if len(universe) > 8 else ''}")
    except Exception:
        pass

    keyboard = [[
        InlineKeyboardButton("🛡️ Conservative", callback_data="mode_conservative"),
        InlineKeyboardButton("⚖️ Semi-Agg", callback_data="mode_semi_aggressive"),
        InlineKeyboardButton("🔥 Aggressive", callback_data="mode_aggressive"),
    ]]
    await _reply(update, fmt(*lines, sep="\n"),
                 reply_markup=InlineKeyboardMarkup(keyboard))


async def setmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch risk profile. Usage: /setmode <conservative|semi_aggressive|aggressive>"""
    if not _is_authorized(update): return
    orch = context.bot_data.get("orchestrator")
    if not orch or not orch.profile_manager:
        await _reply(update, "⚠️ Profile manager not initialized.")
        return

    if not context.args:
        await _reply(update, "Usage: /setmode <conservative|semi_aggressive|aggressive>")
        return

    from src.risk.profile_manager import RiskProfile
    name = context.args[0].lower().replace("-", "_")
    try:
        profile = RiskProfile(name)
    except ValueError:
        await _reply(update, f"❌ Invalid profile: {name}\nValid: conservative, semi_aggressive, aggressive")
        return

    user = update.effective_user
    changed_by = f"tg_{user.id}"
    ok = await orch.profile_manager.set_profile(profile, changed_by, f"Manual via /setmode")
    if not ok:
        await _reply(update, "⏳ Cooldown active — wait 5 minutes between changes.")
        return

    p = await orch.profile_manager.get_active_profile()
    await _reply(update, fmt(bold(f"✅ Switched to {p.emoji} {p.name.upper()}"), sep="\n"),
                 reply_markup=build_main_keyboard())


async def universe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current dynamic crypto universe."""
    if not _is_authorized(update): return
    orch = context.bot_data.get("orchestrator")
    if not orch or not orch.universe_engine:
        await _reply(update, "⚠️ Universe engine not initialized.")
        return

    universe = await orch.universe_engine.get_current()
    lines = [
        bold("📡 Crypto Universe"),
        f"Scanning {len(universe)} coins:",
        "",
    ]
    for i, sym in enumerate(universe, 1):
        lines.append(f"  {i}. {sym}")
    lines.append("")
    lines.append(italic("Refreshes every 4 hours. Use /refresh_universe to force."))

    keyboard = [[InlineKeyboardButton("🔄 Refresh Now", callback_data="universe_refresh")]]
    await _reply(update, fmt(*lines, sep="\n"),
                 reply_markup=InlineKeyboardMarkup(keyboard))


async def refresh_universe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force regenerate the dynamic universe."""
    if not _is_authorized(update): return
    orch = context.bot_data.get("orchestrator")
    if not orch or not orch.universe_engine:
        await _reply(update, "⚠️ Universe engine not initialized.")
        return

    msg = await _reply(update, "🔄 Regenerating universe...")
    try:
        universe = await orch.universe_engine.generate()
        await msg.edit_text(str(fmt(
            bold("✅ Universe Updated"),
            f"Now scanning {len(universe)} coins: {', '.join(universe[:8])}{'...' if len(universe) > 8 else ''}",
            sep="\n"
        )), parse_mode="HTML", reply_markup=build_main_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Universe refresh failed: {e}")


async def _show_universe_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """Show paginated universe detail with per-coin signal status. 5 coins per page."""
    orch = context.bot_data.get("orchestrator")
    if not orch or not orch.universe_engine:
        await _reply(update, "⚠️ Universe engine not initialized.")
        return

    try:
        universe = await orch.universe_engine.get_current()
        scores = await orch.universe_engine.get_universe_with_scores()
        score_map = {c["symbol"]: c for c in scores}
    except Exception:
        universe, score_map = [], {}

    # Query recent signals per ticker (only for visible page)
    signal_map = {}
    per_page = 5
    total = len(universe)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    end = min(start + per_page, total)
    page_coins = universe[start:end]

    try:
        from src.models.database import async_session
        from src.models.tables import Signal
        from sqlalchemy import select, desc
        async with async_session() as session:
            for sym in page_coins:
                result = await session.execute(
                    select(Signal).where(
                        Signal.ticker == sym,
                        Signal.market == "CRYPTO",
                    ).order_by(desc(Signal.created_at)).limit(1)
                )
                sig = result.scalar_one_or_none()
                if sig:
                    signal_map[sym] = {
                        "direction": sig.direction,
                        "confidence": sig.confidence_score,
                        "status": sig.status,
                        "created": sig.created_at.strftime("%m-%d %H:%M") if sig.created_at else "?",
                    }
    except Exception:
        pass

    # Build table
    lines = [
        bold("📡 UNIVERSE DETAIL"),
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Total: {total} coins | Page {page + 1}/{total_pages}",
        "",
        bold("Coin        Score   Vol       24h     Signal"),
        "─" * 50,
    ]

    for i, sym in enumerate(page_coins, start + 1):
        sc = score_map.get(sym, {})
        score = sc.get("score", "?")
        vol = sc.get("volume_24h_usd", 0)
        change = sc.get("price_change_pct", 0)

        sig = signal_map.get(sym)
        if sig:
            sig_icon = "🟢" if sig["direction"] == "LONG" else "🔴" if sig["direction"] == "SHORT" else "⚪"
            sig_text = f"{sig_icon}{sig['direction']}{sig['confidence']}%[{sig['status']}]"
        else:
            sig_text = "—"

        change_str = f"{change:+.1f}%"
        # Format as aligned table row
        coin_col = f"{sym:<12}"
        score_col = f"{score if isinstance(score, str) else f'{score:.0f}':>5}"
        vol_col = f"${vol/1e6:.0f}M" if vol >= 1e6 else f"${vol/1e3:.0f}K"
        lines.append(f"{i:>2}. {coin_col}{score_col}  {vol_col:>8}  {change_str:>7}  {sig_text}")

    lines.append("")
    lines.append(italic("Refresh to regenerate universe."))

    # Pagination buttons
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"univ_page_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"univ_page_{page + 1}"))

    keyboard = [
        nav,
        [InlineKeyboardButton("🔄 Refresh Universe", callback_data="universe_refresh")],
        [InlineKeyboardButton("🎛️ Control Panel", callback_data="cmd_control")],
    ]
    await _reply(update, fmt(*lines, sep="\n"),
                 reply_markup=InlineKeyboardMarkup(keyboard))


# --- Autonomous Session Manager (ASM) View ---

async def _asm_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show ASM status with Start/Stop/Refresh buttons."""
    r = _get_redis(context)
    bybit = _get_bybit(context)
    orch = context.bot_data.get("orchestrator")

    from src.agents.autonomous_session import AutonomousSessionManager, REDIS_ACTIVE
    asm = AutonomousSessionManager(orch, r, bybit)

    is_active = await asm.is_active()

    if is_active:
        status_text = await asm.get_status()
        keyboard = [
            [InlineKeyboardButton("⏹ Stop", callback_data="auto_stop"),
             InlineKeyboardButton("🔄 Refresh", callback_data="auto_refresh")],
            [InlineKeyboardButton("🏠 Dashboard", callback_data="cmd_dashboard")]
        ]
    else:
        # Config menu for launching new session
        wallet_balance = 0.0
        try:
            wallet = await bybit.get_wallet_balance()
            wallet_balance = float(wallet.get("balance", 0) or 0)
        except Exception:
            pass

        from src.utils.formatters import format_risk_button_text

        status_text = fmt(
            bold("⚙️ Session Configuration"), "\n",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
            "Please configure your risk parameters.\n",
            italic("⚠️ Session runs continuously until manually stopped or halted by risk limits."), "\n\n",
            bold(f"💰 Current Wallet: ${wallet_balance:,.2f}"), "\n",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
            bold("Select Risk Level:"),
        )

        keyboard = [
            [InlineKeyboardButton(format_risk_button_text(10, wallet_balance), callback_data="auto_start_10"),
             InlineKeyboardButton(format_risk_button_text(30, wallet_balance), callback_data="auto_start_30")],
            [InlineKeyboardButton(format_risk_button_text(50, wallet_balance), callback_data="auto_start_50"),
             InlineKeyboardButton(format_risk_button_text(70, wallet_balance), callback_data="auto_start_70")],
            [InlineKeyboardButton(format_risk_button_text(100, wallet_balance), callback_data="auto_start_100")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cmd_dashboard")]
        ]

    await _reply(update, HTML(status_text), reply_markup=InlineKeyboardMarkup(keyboard))


async def _auto_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start autonomous session with chosen risk level."""
    r = _get_redis(context)
    bybit = _get_bybit(context)
    orch = context.bot_data.get("orchestrator")
    chat_id = update.effective_chat.id
    data = update.callback_query.data

    # Parse risk from callback: "auto_start_30" -> 30.0
    try:
        risk_pct = float(data.split("_")[-1])
    except (ValueError, IndexError):
        risk_pct = 10.0

    from src.agents.autonomous_session import AutonomousSessionManager
    asm = AutonomousSessionManager(orch, r, bybit)
    # Read duration from Redis (set by auto_dur_* callback)
    duration_min = 0
    try:
        dur_val = await r.get("karsa:auto:pending_duration_min")
        if dur_val:
            duration_min = int(dur_val)
            await r.delete("karsa:auto:pending_duration_min")
    except Exception:
        pass
    result = await asm.start(chat_id, {"risk_pct": risk_pct, "max_pos": 3, "interval": 15, "duration_min": duration_min})

    keyboard = [
        [InlineKeyboardButton("⏹ Stop", callback_data="auto_stop"),
         InlineKeyboardButton("🔄 Refresh", callback_data="auto_refresh")],
        [InlineKeyboardButton("🏠 Dashboard", callback_data="cmd_dashboard")]
    ]
    await _reply(update, HTML(result), reply_markup=InlineKeyboardMarkup(keyboard))


async def _auto_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop autonomous session and show MTM report."""
    r = _get_redis(context)
    bybit = _get_bybit(context)
    orch = context.bot_data.get("orchestrator")

    from src.agents.autonomous_session import AutonomousSessionManager
    asm = AutonomousSessionManager(orch, r, bybit)
    result = await asm.stop()

    # After session stops, show launch screen
    keyboard = [
        [InlineKeyboardButton("🚀 LAUNCH NEW SESSION", callback_data="auto_launch")],
        [InlineKeyboardButton("📜 Trade History", callback_data="cmd_trade_history"),
         InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings")],
    ]
    await _reply(update, HTML(result), reply_markup=InlineKeyboardMarkup(keyboard))


async def _auto_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force refresh ASM status (bypass cooldown)."""
    await _asm_view(update, context)


async def _auto_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pause autonomous session — freeze scanning loop, keep positions open."""
    r = _get_redis(context)
    bybit = _get_bybit(context)
    orch = context.bot_data.get("orchestrator")

    from src.agents.autonomous_session import AutonomousSessionManager
    asm = AutonomousSessionManager(orch, r, bybit)
    result = await asm.pause()

    keyboard = [
        [InlineKeyboardButton("▶️ Resume", callback_data="auto_resume_pause"),
         InlineKeyboardButton("🛑 Stop Session", callback_data="auto_stop")],
        [InlineKeyboardButton("🏠 Back to Dashboard", callback_data="cmd_dashboard")],
    ]
    await _reply(update, HTML(result), reply_markup=InlineKeyboardMarkup(keyboard))


async def _auto_resume_from_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume a paused autonomous session."""
    r = _get_redis(context)
    bybit = _get_bybit(context)
    orch = context.bot_data.get("orchestrator")

    from src.agents.autonomous_session import AutonomousSessionManager
    asm = AutonomousSessionManager(orch, r, bybit)
    result = await asm.resume()

    keyboard = [
        [InlineKeyboardButton("⏸ Pause", callback_data="auto_pause"),
         InlineKeyboardButton("🛑 Stop Session", callback_data="auto_stop")],
        [InlineKeyboardButton("🏠 Back to Dashboard", callback_data="cmd_dashboard")],
    ]
    await _reply(update, HTML(result), reply_markup=InlineKeyboardMarkup(keyboard))


async def _toggle_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle trade alert notifications on/off."""
    r = _get_redis(context)
    current = await r.get("karsa:alerts_enabled")
    is_on = current in ("1", b"1") if current is not None else True
    await r.set("karsa:alerts_enabled", "0" if is_on else "1")
    status = "🔕 Alerts Muted" if is_on else "🔔 Alerts Enabled"
    await update.callback_query.edit_message_text(
        f"<b>{status}</b>",
        parse_mode="HTML",
        reply_markup=build_main_keyboard(),
    )


# --- /clear_halt command (P0 safety) ---

async def clear_halt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear emergency/halt state. Admin-only command."""
    if not _is_authorized(update): return

    try:
        from src.risk.emergency import deactivate_global_halt, deactivate
        await deactivate_global_halt(operator=f"tg_{update.effective_user.id}")
        try:
            await deactivate(operator=f"tg_{update.effective_user.id}")
        except Exception:
            pass
        r = _get_redis(context)
        await r.delete("karsa:crypto_cooldown")
        await _reply(update, "✅ <b>Halt cleared.</b> Trading can resume.", reply_markup=build_main_keyboard())
    except Exception as e:
        await _reply(update, f"❌ Failed to clear halt: {e}")


# --- View Positions with Move SL to BE (P3.3) ---

async def view_positions_detail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detailed position view with Move SL to BE buttons and position proportions."""
    if not _is_authorized(update): return

    bybit = _get_bybit(context)
    positions = []
    try:
        raw = await bybit.get_positions()
        positions = [p for p in raw if float(p.get("size", 0)) > 0]
    except Exception:
        pass

    # Get wallet balance for proportion calculation
    total_equity = 0.0
    try:
        wallet = await bybit.get_wallet_balance()
        total_equity = float(wallet.get("balance", 0) or 0)
    except Exception:
        pass

    from src.utils.formatters import format_position_card

    lines = [
        bold("📊 OPEN POSITIONS DETAIL"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
    ]

    # Calculate total position value and cash
    total_position_value = 0.0
    position_values = []
    for p in positions:
        entry = float(p.get("entry_price", 0) or 0)
        size = float(p.get("size", 0) or 0)
        pos_value = entry * size
        total_position_value += pos_value
        position_values.append(pos_value)

    cash = total_equity - total_position_value if total_equity > 0 else 0
    cash_pct = (cash / total_equity * 100) if total_equity > 0 else 0

    # Show allocation summary
    if total_equity > 0 and positions:
        lines.append(bold("💰 ALLOCATION"))
        lines.append(f"Equity: ${total_equity:,.2f} | Cash: ${cash:,.2f} ({cash_pct:.1f}%)")
        lines.append(f"Positions: {len(positions)} | Deployed: ${total_position_value:,.2f} ({100-cash_pct:.1f}%)")
        lines.append("")

    keyboard = []

    if not positions:
        lines.append("No open positions.")
    else:
        for i, (p, pos_val) in enumerate(zip(positions, position_values), 1):
            pos_pct = (pos_val / total_equity * 100) if total_equity > 0 else 0
            card = format_position_card(p, index=i, pos_pct=pos_pct)
            lines.append(card)
            lines.append("")

            symbol = p.get("symbol", "?")
            # Close and Move SL to BE buttons per position
            keyboard.append([
                InlineKeyboardButton(f"🏃 Close {symbol}", callback_data=f"close_pos_{symbol}"),
                InlineKeyboardButton(f"🛡 SL→BE {symbol}", callback_data=f"move_sl_be_{symbol}"),
            ])

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(italic("💡 Move SL to BE shifts Stop Loss to Entry Price — risk-free trade."))

    keyboard.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="cmd_dashboard")])

    await send_or_edit_message(update, fmt(*lines, sep="\n"), reply_markup=InlineKeyboardMarkup(keyboard))


async def _move_sl_to_be(update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str):
    """Move stop loss to breakeven (entry price) for a specific position."""
    bybit = _get_bybit(context)

    try:
        # Get position details
        positions = await bybit.get_positions()
        pos = None
        for p in (positions or []):
            if p.get("symbol") == symbol and float(p.get("size", 0)) > 0:
                pos = p
                break

        if not pos:
            await _reply(update, f"❌ No open position found for {symbol}")
            return

        entry_price = float(pos.get("entry_price", 0))
        if entry_price <= 0:
            await _reply(update, f"❌ Cannot determine entry price for {symbol}")
            return

        # Amend stop loss order on Bybit
        side = pos.get("side", "Buy")
        # For LONG: SL goes to entry (below current). For SHORT: SL goes to entry (above current)
        new_sl = entry_price

        # Use Bybit API to amend the SL order
        try:
            # Try to find existing SL order and amend it
            orders = await bybit.get_open_orders(symbol)
            sl_order = None
            for o in (orders or []):
                if o.get("stopLoss") or o.get("order_type") == "Stop":
                    sl_order = o
                    break

            if sl_order:
                # Amend existing SL
                order_id = sl_order.get("order_id", "")
                await bybit.amend_order(
                    symbol=symbol,
                    order_id=order_id,
                    stop_loss=str(new_sl),
                )
            else:
                # Set new SL if none exists
                await bybit.set_stop_loss(
                    symbol=symbol,
                    side="Sell" if side == "Buy" else "Buy",
                    stop_price=str(new_sl),
                )
        except Exception as amend_err:
            logger.warning("move_sl_be_amend_failed", symbol=symbol, error=str(amend_err))
            # Fallback: try set_stop_loss directly
            try:
                await bybit.set_stop_loss(
                    symbol=symbol,
                    side="Sell" if side == "Buy" else "Buy",
                    stop_price=str(new_sl),
                )
            except Exception:
                await _reply(update, f"❌ Failed to amend SL for {symbol}: {amend_err}")
                return

        # Edit position card in-place (refresh the positions view)
        await view_positions_detail_cmd(update, context)

        # Send toast confirmation
        chat_id = update.effective_chat.id
        toast_text = fmt(
            bold("✅ SL Moved to Breakeven"), "\n",
            f"Symbol: {symbol}", "\n",
            f"New SL: ${new_sl:,.2f}",
        )
        toast_msg = await send_toast(context.bot, chat_id, str(toast_text))
        if toast_msg:
            # Add dismiss button to toast
            dismiss_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Dismiss", callback_data=f"dismiss_toast_{toast_msg.message_id}")]
            ])
            try:
                await toast_msg.edit_reply_markup(reply_markup=dismiss_kb)
            except Exception:
                pass

    except Exception as e:
        logger.error("move_sl_be_failed", symbol=symbol, error=str(e))
        await _reply(update, f"❌ Move SL to BE failed: {e}")


async def _close_position(update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str):
    """Market close a specific position."""
    try:
        from src.risk.sor import SmartOrderRouter
        bybit = _get_bybit(context)
        sor = SmartOrderRouter(bybit)

        # Get position side
        positions = await bybit.get_positions()
        pos = None
        for p in (positions or []):
            if p.get("symbol") == symbol and float(p.get("size", 0)) > 0:
                pos = p
                break

        if not pos:
            await _reply(update, f"❌ No open position for {symbol}")
            return

        side = pos.get("side", "Buy")
        size = float(pos.get("size", 0))
        close_side = "Sell" if side == "Buy" else "Buy"

        await bybit.place_order(
            symbol=symbol,
            side=close_side,
            order_type="Market",
            qty=str(size),
        )

        await _reply(update, f"🏃 <b>{symbol}</b> position closed (Market {close_side})", reply_markup=build_main_keyboard())

    except Exception as e:
        logger.error("close_position_failed", symbol=symbol, error=str(e))
        await _reply(update, f"❌ Failed to close {symbol}: {e}")


# --- Trade History (Paginated) ---

async def _fetch_trade_history_page(page: int = 1):
    """Fetch a page of trades + summary stats. Returns (trades, total, wins, losses, net_pnl)."""
    from src.models.database import async_session
    from src.models.tables import ClosedPaperTrade
    from sqlalchemy import select, desc, func

    PAGE_SIZE = 5
    offset = (page - 1) * PAGE_SIZE

    async with async_session() as session:
        # Fetch page slice
        result = await session.execute(
            select(ClosedPaperTrade)
            .where(ClosedPaperTrade.market == "CRYPTO")
            .order_by(desc(ClosedPaperTrade.exit_date))
            .limit(PAGE_SIZE)
            .offset(offset)
        )
        trades = result.scalars().all()

        # Count total trades
        total_result = await session.execute(
            select(func.count(ClosedPaperTrade.id))
            .where(ClosedPaperTrade.market == "CRYPTO")
        )
        total = total_result.scalar() or 0

        # Count wins (pnl > 0)
        wins_result = await session.execute(
            select(func.count(ClosedPaperTrade.id))
            .where(ClosedPaperTrade.market == "CRYPTO")
            .where(ClosedPaperTrade.realized_pnl_pct > 0)
        )
        wins = wins_result.scalar() or 0
        losses = total - wins

        # Sum net PnL
        pnl_result = await session.execute(
            select(func.sum(ClosedPaperTrade.realized_pnl))
            .where(ClosedPaperTrade.market == "CRYPTO")
        )
        net_pnl = float(pnl_result.scalar() or 0)

    return trades, total, wins, losses, net_pnl


async def trade_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show paginated trade history with Prev/Next navigation."""
    if not _is_authorized(update): return

    try:
        from src.utils.formatters.trade_history_formatter import TradeHistoryFormatter

        trades, total, wins, losses, net_pnl = await _fetch_trade_history_page(1)
        text, keyboard = TradeHistoryFormatter.build_message(trades, 1, total, wins, losses, net_pnl)

        # parse_mode=None — pure Unicode, immune to AI < > formatting crashes
        await send_or_edit_message(update, text, reply_markup=keyboard, parse_mode=None)

    except Exception as e:
        logger.error("trade_history_failed", error=str(e), exc_info=True)
        error_detail = str(e)[:120] if str(e) else type(e).__name__
        await _reply(update, f"❌ Trade history load failed: {error_detail}", reply_markup=InlineKeyboardMarkup(
                         [[InlineKeyboardButton("🏠 Back to Dashboard", callback_data="cmd_dashboard")]]))


# --- Global Callback Router ---

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    r = _get_redis(context)  # define once — used by auto_dur_ and other inline branches

    # 5 Core Views
    if data == "cmd_dashboard": await dashboard_cmd(update, context)
    elif data == "cmd_activity": await activity_cmd(update, context)
    elif data == "cmd_portfolio": await portfolio_cmd(update, context)
    elif data == "cmd_performance": await performance_cmd(update, context)
    elif data == "cmd_control": await control_cmd(update, context)
    elif data == "cmd_auto": await _asm_view(update, context)
    elif data == "cmd_settings": await settings_cmd(update, context)
    elif data == "toggle_max_pos": await _toggle_max_pos(update, context)
    elif data == "toggle_regime": await _toggle_regime(update, context)
    elif data == "toggle_risk_profile": await _toggle_risk_profile(update, context)

    # ASM Dashboard views
    elif data == "cmd_history": await session_history_cmd(update, context)
    elif data.startswith("cmd_history_p"): await session_history_cmd(update, context)
    elif data.startswith("session_detail_"): await session_detail_cmd(update, context)
    elif data.startswith("rerun_cfg_"):
        session_id = int(data.replace("rerun_cfg_", ""))
        from src.models.database import async_session
        from src.models.tables import CryptoAutoSession
        from sqlalchemy import select
        async with async_session() as session:
            res = await session.execute(select(CryptoAutoSession).where(CryptoAutoSession.id == session_id))
            row = res.scalar_one_or_none()
            if row and row.config:
                from src.agents.autonomous_session import AutonomousSessionManager
                asm = AutonomousSessionManager(context.bot_data.get("orchestrator"), r, _get_bybit(context))
                chat_id = update.effective_chat.id
                result = await asm.start(chat_id, row.config)
                await query.edit_message_text(result, parse_mode="HTML", reply_markup=build_main_keyboard())
            else:
                await query.edit_message_text("❌ Session config not found.")
    elif data == "cmd_profiles": await manage_profiles_cmd(update, context)
    elif data == "cmd_positions": await open_positions_cmd(update, context)
    elif data == "view_positions_detail": await view_positions_detail_cmd(update, context)
    elif data == "cmd_trade_history": await trade_history_cmd(update, context)
    elif data.startswith("karsa:history:page:"):
        try:
            page = int(data.split(":")[-1])
            from src.utils.formatters.trade_history_formatter import TradeHistoryFormatter
            trades, total, wins, losses, net_pnl = await _fetch_trade_history_page(page)
            text, keyboard = TradeHistoryFormatter.build_message(trades, page, total, wins, losses, net_pnl)
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=None)
        except Exception as e:
            logger.error("history_pagination_failed", error=str(e))
            await query.answer("Failed to load page", show_alert=True)
    elif data == "noop":
        await query.answer()
    elif data.startswith("move_sl_be_"):
        symbol = data.replace("move_sl_be_", "")
        await _move_sl_to_be(update, context, symbol)
    elif data.startswith("close_pos_"):
        symbol = data.replace("close_pos_", "")
        await _close_position(update, context, symbol)
    elif data.startswith("dismiss_toast_"):
        try:
            msg_id = int(data.replace("dismiss_toast_", ""))
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
        except Exception:
            pass
    elif data == "auto_launch": await _asm_view(update, context)
    elif data == "auto_pause": await _auto_pause(update, context)
    elif data == "auto_resume_pause": await _auto_resume_from_pause(update, context)
    elif data.startswith("profile_"):
        from src.risk.profile_manager import RiskProfile
        profile_name = data.replace("profile_", "")
        try:
            profile = RiskProfile(profile_name)
        except ValueError:
            await query.edit_message_text("❌ Invalid profile")
            return
        orch = context.bot_data.get("orchestrator")
        if orch and orch.profile_manager:
            user = query.from_user
            ok = await orch.profile_manager.set_profile(profile, f"tg_{user.id}", "Dashboard")
            if not ok:
                await query.edit_message_text("⏳ Cooldown active — wait 5 minutes.")
                return
            p = await orch.profile_manager.get_active_profile()
            await manage_profiles_cmd(update, context)

    # Autonomous Session Manager
    elif data.startswith("auto_dur_"):
        # Duration selection — store in Redis, user still needs to pick risk level
        try:
            dur_min = int(data.split("_")[-1])
            await r.set("karsa:auto:pending_duration_min", str(dur_min))
            label = "Unlimited" if dur_min == 0 else f"{dur_min // 60}h" if dur_min >= 60 else f"{dur_min}m"
            await query.edit_message_text(
                str(fmt(bold(f"⏰ Duration set: {label}"), "\nNow select a risk level above.", sep="\n")),
                parse_mode="HTML",
                reply_markup=query.message.reply_markup,
            )
        except Exception:
            pass
    elif data.startswith("auto_start_"): await _auto_start(update, context)
    elif data == "auto_stop": await _auto_stop(update, context)
    elif data == "auto_refresh": await _auto_refresh(update, context)
    elif data == "toggle_alerts": await _toggle_alerts(update, context)

    # Operations
    elif data == "crypto_kill": await _execute_kill(update, context)
    elif data == "crypto_sellall": await _execute_sellall(update, context)
    elif data == "crypto_resume": await _execute_resume(update, context)
    elif data == "crypto_walkforward": await walkforward_cmd(update, context)

    # Risk Profile switching
    elif data.startswith("mode_"):
        from src.risk.profile_manager import RiskProfile
        profile_name = data.replace("mode_", "")
        try:
            profile = RiskProfile(profile_name)
        except ValueError:
            await query.edit_message_text("❌ Invalid profile")
            return
        orch = context.bot_data.get("orchestrator")
        if orch and orch.profile_manager:
            user = query.from_user
            ok = await orch.profile_manager.set_profile(profile, f"tg_{user.id}", "Inline keyboard")
            if not ok:
                await query.edit_message_text("⏳ Cooldown active — wait 5 minutes.")
                return
            p = await orch.profile_manager.get_active_profile()
            await query.edit_message_text(
                str(fmt(bold(f"✅ Switched to {p.emoji} {p.name.upper()}"), sep="\n")),
                parse_mode="HTML", reply_markup=build_main_keyboard())

    # Universe refresh
    elif data == "universe_refresh":
        orch = context.bot_data.get("orchestrator")
        if orch and orch.universe_engine:
            try:
                universe = await orch.universe_engine.generate()
                await query.edit_message_text(
                    str(fmt(bold("✅ Universe Updated"),
                           f"Scanning {len(universe)} coins: {', '.join(universe[:8])}",
                           sep="\n")),
                    parse_mode="HTML", reply_markup=build_main_keyboard())
            except Exception:
                await query.edit_message_text("❌ Refresh failed")

    # Universe detail
    elif data == "universe_detail":
        await _show_universe_detail(update, context, page=0)

    # Universe pagination
    elif data.startswith("univ_page_"):
        try:
            page_num = int(data.replace("univ_page_", ""))
            await _show_universe_detail(update, context, page=page_num)
        except (ValueError, IndexError):
            pass

    # Noop (page indicator button)
    elif data == "noop":
        pass

# Add fallback routing for root commands matching the UI structure
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await dashboard_cmd(update, context)


# --- /replay command — reconstruct position timeline from event store ---

async def replay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show event timeline for a position ticker."""
    ticker = context.args[0].upper() if context.args else ""
    if not ticker:
        await update.message.reply_text("Usage: /replay BTCUSDT")
        return

    try:
        # Get replay engine from main_crypto orchestrator
        from src.main_crypto import karsa_app
        if not karsa_app or not hasattr(karsa_app, 'replay_engine'):
            await update.message.reply_text("Replay engine not available")
            return

        result = karsa_app.replay_engine.replay(ticker)
        if not result.timeline:
            await update.message.reply_text(f"No events found for {ticker}")
            return

        lines = [f"📋 <b>{ticker} Event Timeline</b>\n"]
        for i, entry in enumerate(result.timeline, 1):
            emoji = {
                "PositionOpened": "🟢", "PositionReduced": "🟡",
                "PositionClosed": "🔴", "TrailingActivated": "📈",
                "BreakEvenActivated": "🔒", "StopLossTriggered": "⛔",
                "StopLossRecovered": "🛡️", "StopLossUpdated": "📝",
                "PositionSynced": "🔄",
            }.get(entry["event_type"], "📌")
            lines.append(f"{i}. {emoji} {entry['event_type']}")
            if entry.get("publisher"):
                lines.append(f"   by: {entry['publisher']}")
            if entry.get("payload"):
                for k, v in entry["payload"].items():
                    lines.append(f"   {k}: {v}")

        text = "\n".join(lines[:30])  # limit to 30 lines
        if len(result.timeline) > 30:
            text += f"\n\n... and {len(result.timeline) - 30} more events"

        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Replay error: {e}")


# --- /events command — recent event history from Redis ---

async def events_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent architecture events from Redis history."""
    limit = 10
    if context.args:
        try:
            limit = min(int(context.args[0]), 30)
        except ValueError:
            pass

    try:
        import redis.asyncio as redis
        from src.config import settings
        from src.architecture.events.redis_bus import get_event_history

        r = redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            events = await get_event_history(r, limit)

            if not events:
                await update.message.reply_text("No events in history yet")
                return

            lines = [f"📊 <b>Recent Events ({len(events)})</b>\n"]
            emoji_map = {
                "PositionOpened": "🟢", "PositionReduced": "🟡",
                "PositionClosed": "🔴", "TrailingActivated": "📈",
                "BreakEvenActivated": "🔒", "StopLossTriggered": "⛔",
                "StopLossRecovered": "🛡️", "TestCrossProcess": "🧪",
            }
            for ev in events:
                e = emoji_map.get(ev.get("event_type", ""), "📌")
                lines.append(f"{e} {ev.get('event_type')} → {ev.get('aggregate_id')}")
                if ev.get("publisher"):
                    lines.append(f"   by: {ev['publisher']}")

            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        finally:
            await r.close()
    except Exception as e:
        await update.message.reply_text(f"Events error: {e}")
