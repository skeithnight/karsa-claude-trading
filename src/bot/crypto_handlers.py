"""Karsa Trading System - Crypto Telegram Bot Handlers (Simplified)"""

import json
import redis.asyncio as redis
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.config import settings
from src.utils.format import HTML, bold, italic, code, pre, fmt, join
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
    """Unified navigation keyboard used across all 5 core views."""
    keyboard = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="cmd_dashboard"),
         InlineKeyboardButton("📋 Activity", callback_data="cmd_activity")],
        [InlineKeyboardButton("💼 Portfolio", callback_data="cmd_portfolio"),
         InlineKeyboardButton("📈 Performance", callback_data="cmd_performance")],
        [InlineKeyboardButton("📡 Universe Detail", callback_data="universe_detail"),
         InlineKeyboardButton("🎛️ Control", callback_data="cmd_control")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- 1. Dashboard (Command Center) ---

async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update): return
    
    redis_ok, bybit_ok, db_ok, halt_active = False, False, False, False
    wallet, regime_state, hurst, adx, rec = {}, "UNKNOWN", 0.5, 0.0, ""
    
    try:
        r = _get_redis(context)
        redis_ok = await r.ping()
        halt_active = bool(await r.get("karsa:global_halt"))
    except Exception: pass

    try:
        from src.models.database import async_session
        from sqlalchemy import text
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
            db_ok = True
    except Exception: pass

    try:
        bybit = _get_bybit(context)
        wallet = await bybit.get_wallet_balance()
        bybit_ok = not wallet.get("error")
        
        orch = context.bot_data.get("orchestrator")
        from src.advisory.crypto_regime import CryptoRegimeFilter
        regime = await CryptoRegimeFilter(orch.mcp).get_current_regime()
        regime_state = regime.get("state", "UNKNOWN")
        hurst = regime.get("hurst", 0.5)
        adx = regime.get("adx", 0.0)
        rec = regime.get("recommendation", "")
    except Exception: pass

    sys_status = (
        f"Bybit: {'🟢 Connected' if bybit_ok else '🔴 Off'} | DB: {'🟢' if db_ok else '🔴'} | Redis: {'🟢' if redis_ok else '🔴'}\n"
    )
    regime_display = f"Regime: {'🟢' if 'BULL' in regime_state else '🔴' if 'BEAR' in regime_state else '🟡'} {regime_state} (Hurst: {hurst:.2f}, ADX: {adx:.0f})"
    
    wallet_block = (
        f"Balance   : ${wallet.get('balance', 0):,.2f}\n"
        f"Available : ${wallet.get('available', 0):,.2f}\n"
        f"Margin    : ${wallet.get('used_margin', 0):,.2f} "
        f"({(wallet.get('used_margin', 0)/max(wallet.get('balance', 1), 1)*100):.1f}%)\n"
    )

    # Risk profile
    profile_line = ""
    try:
        orch = context.bot_data.get("orchestrator")
        if orch and orch.profile_manager:
            p = await orch.profile_manager.get_active_profile()
            profile_line = f"{p.emoji} {p.name.upper().replace('_', ' ')}"
    except Exception: pass

    # Universe
    universe_line = ""
    try:
        orch = context.bot_data.get("orchestrator")
        if orch and orch.universe_engine:
            universe = await orch.universe_engine.get_current()
            universe_line = f"📡 {len(universe)} coins: {', '.join(universe[:6])}{'...' if len(universe) > 6 else ''}"
    except Exception: pass

    text = fmt(
        bold("🖥️ KARSA DESK DASHBOARD"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
        bold("System Vitals:"), "\n", pre(sys_status), "\n",
        bold("Market State:"), "\n", pre(regime_display), "\n",
        bold("Risk Profile:"), "\n", profile_line, "\n",
        bold("Universe:"), "\n", universe_line, "\n",
        bold("Capital:"), "\n", pre(wallet_block), "\n",
        f"Halt Switch: {'🚨 ACTIVE' if halt_active else '🟢 Standard Operations'}"
    )
    await _reply(update, text, reply_markup=build_main_keyboard())

# --- 2. Activity (Live Chronological Feed) ---

async def activity_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update): return
    try:
        from src.models.database import async_session
        from src.models.tables import Signal, ClosedPaperTrade, CryptoPosition
        from sqlalchemy import select, desc
        
        async with async_session() as session:
            sig_result = await session.execute(
                select(Signal).where(Signal.market == "CRYPTO")
                .order_by(desc(Signal.created_at)).limit(5)
            )
            signals = sig_result.scalars().all()

            trade_result = await session.execute(
                select(ClosedPaperTrade).where(ClosedPaperTrade.market == "CRYPTO")
                .order_by(desc(ClosedPaperTrade.exit_date)).limit(3)
            )
            trades = trade_result.scalars().all()
            
        lines = [bold("📋 LIVE ACTIVITY FEED"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"]
        
        feed = []
        for s in signals:
            ts = s.created_at.strftime("%H:%M") if s.created_at else "?"
            emoji = "✅" if s.status == "EXECUTED" else "⛔" if s.status == "REJECTED" else "🔍"
            side = "🟢 LONG" if s.direction == "LONG" else "🔴 SHORT"
            text_block = f"`{ts}` {emoji} **{s.status}** {s.ticker} {side}\nConf: {s.confidence_score}%"
            if s.status == "EXECUTED": text_block += f" | Fill: ${s.entry_price:,.2f}"
            full_r = s.reasoning or "No thesis."
            text_block += f"\n_Thesis: {full_r[:75]}..._"
            feed.append((s.created_at, text_block))
            
        for t in trades:
            ts = t.exit_date.strftime("%H:%M") if t.exit_date else "?"
            emoji = "🟢" if (t.realized_pnl_pct or 0) > 0 else "🔴"
            text_block = f"`{ts}` 💰 **CLOSED** {t.ticker} {t.side}\nPnL: {emoji} {t.realized_pnl_pct:+.2f}% | _Reason: {t.exit_reason}_"
            feed.append((t.exit_date, text_block))
            
        feed.sort(key=lambda x: x[0], reverse=True)
        
        for _, block in feed[:7]:
            lines.append(block + "\n\n")
            
        if not feed: lines.append("📭 No recent activity.")
            
        await _reply(update, str("\n".join(lines)), parse_mode="Markdown", reply_markup=build_main_keyboard())
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

    state_block = (
        f"Global Halt: {'🚨 ACTIVE' if halt_active else '🟢 INACTIVE'}\n"
        f"Cooldown: {'⏳ ACTIVE' if cooldown else '🟢 INACTIVE'}"
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
            universe_block = f"{len(universe)} coins: {', '.join(universe[:8])}{'...' if len(universe) > 8 else ''}"
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


# --- Global Callback Router ---

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # 5 Core Views
    if data == "cmd_dashboard": await dashboard_cmd(update, context)
    elif data == "cmd_activity": await activity_cmd(update, context)
    elif data == "cmd_portfolio": await portfolio_cmd(update, context)
    elif data == "cmd_performance": await performance_cmd(update, context)
    elif data == "cmd_control": await control_cmd(update, context)

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
