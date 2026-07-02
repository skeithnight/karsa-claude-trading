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
        [InlineKeyboardButton("🎛️ Control", callback_data="cmd_control")]
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

    text = fmt(
        bold("🖥️ KARSA DESK DASHBOARD"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
        bold("System Vitals:"), "\n", pre(sys_status), "\n",
        bold("Market State:"), "\n", pre(regime_display), "\n",
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
    
    text = fmt(
        bold("🎛️ DESK CONTROL PANEL"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
        bold("States:"), "\n", pre(state_block), "\n\n",
        italic("Select an operation below. Warning: Kill options are immediate.")
    )
    
    keyboard = [
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

# Add fallback routing for root commands matching the UI structure
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await dashboard_cmd(update, context)
