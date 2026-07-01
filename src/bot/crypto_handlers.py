"""Karsa Trading System - Crypto Telegram Bot Handlers (Separate Instance)"""

import redis.asyncio as redis
from telegram import Update
from telegram.ext import ContextTypes

from src.config import settings
from src.utils.format import HTML, bold, italic, code, pre, fmt, join
from src.utils.logging import get_logger

logger = get_logger("crypto_handlers")


def _get_bybit(context: ContextTypes.DEFAULT_TYPE):
    """Get BybitClient via orchestrator's MCPClient (reuses connections)."""
    orch = context.bot_data.get("orchestrator")
    if orch:
        return orch.mcp._get_bybit()
    raise RuntimeError("Orchestrator not connected — cannot access BybitClient")


def _get_redis(context: ContextTypes.DEFAULT_TYPE):
    """Get shared Redis client from bot_data (no connection leak)."""
    client = context.bot_data.get("redis_client")
    if client:
        return client
    # Fallback: create one (should not happen if crypto_main.py is used)
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def _is_authorized(update: Update) -> bool:
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if not settings.TELEGRAM_CHAT_ID:
        logger.error("telegram_chat_id_not_configured")
        return False
    if chat_id != str(settings.TELEGRAM_CHAT_ID):
        logger.warning("unauthorized_chat")
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
        return await update.callback_query.message.edit_text(text, **kwargs)
    elif update.message:
        return await update.message.reply_text(text, **kwargs)
    return None


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    from src.utils.telegram_helpers import build_nav_keyboard
    text = fmt(
        bold("🖥️ KARSA CRYPTO DESK — ONLINE"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
        "Welcome to the autonomous crypto execution desk.\n",
        "Strategies run 24/7 on Bybit perpetuals with vol-targeted risk.\n\n",
        bold("Available Commands:"), "\n",
        code("/briefing"), " — Morning/evening market digest\n",
        code("/market <coin>"), " — Full technical/crowding analysis\n",
        code("/status"), " — System vitals and wallet balance\n",
        code("/portfolio"), " — Live positions, entry levels, and uPnL\n",
        code("/scan <coin>"), " — Force-scan single coin or run universe scan\n",
        code("/pnl"), " — Closed trades and statistics\n",
        code("/risk"), " — Cooldowns and limits\n",
        code("/kill"), " — Emergency stop + flatten all\n"
    )
    keyboard = build_nav_keyboard([
        [("🌐 Briefing", "cmd_briefing"), ("📊 Status", "cmd_status")],
        [("💼 Portfolio", "cmd_portfolio"), ("📋 Activity", "cmd_activity")],
        [("📖 Guide", "cmd_guide")]
    ])
    await _reply(update, text, reply_markup=keyboard)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return

    redis_ok = False
    bybit_ok = False
    regime_state = "UNKNOWN"
    db_ok = False
    hurst = 0.5
    adx = 0.0
    rec = ""

    r = _get_redis(context)
    try:
        redis_ok = await r.ping()
    except Exception:
        pass

    try:
        from src.models.database import async_session
        from sqlalchemy import text
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass

    wallet = {"balance": 0, "available": 0, "used_margin": 0, "unrealized_pnl": 0}
    api_key_valid = None
    try:
        bybit = _get_bybit(context)
        wallet = await bybit.get_wallet_balance()
        bybit_ok = not wallet.get("error")

        # Validate API key
        api_key_valid = await bybit.validate_api_key()

        from src.advisory.crypto_regime import CryptoRegimeFilter
        orch = context.bot_data.get("orchestrator")
        regime_filter = CryptoRegimeFilter(orch.mcp)
        regime = await regime_filter.get_current_regime()
        regime_state = regime.get("state", "UNKNOWN")
        hurst = regime.get("hurst", 0.5)
        adx = regime.get("adx", 0.0)
        rec = regime.get("recommendation", "")
    except Exception:
        pass

    halt_active = False
    try:
        halt_active = bool(await r.get("karsa:global_halt"))
    except Exception:
        pass

    from src.utils.trader_format import regime_banner

    sys_status = (
        f"Database : {'🟢 Online' if db_ok else '🔴 Offline'}\n"
        f"Redis    : {'🟢 Online' if redis_ok else '🔴 Offline'}\n"
        f"Bybit    : {'🟢 Connected' if bybit_ok else '🔴 Connection Failed'} ({'Testnet' if settings.BYBIT_TESTNET else 'Mainnet'})\n"
        f"Halt Switch: {'🚨 HALTED' if halt_active else '🟢 Standard Operations'}"
    )
    if api_key_valid and not api_key_valid.get("valid"):
        sys_status += f"\nAPI Key  : 🔴 {api_key_valid.get('error', 'Invalid')}"

    wallet_lines = [
        f"Balance   : ${wallet.get('balance', 0):,.2f}",
        f"Available : ${wallet.get('available', 0):,.2f}",
        f"Margin    : ${wallet.get('used_margin', 0):,.2f}",
        f"Unrealized: {'🟢' if wallet.get('unrealized_pnl', 0) >= 0 else '🔴'} ${wallet.get('unrealized_pnl', 0):+,.2f}"
    ]
    # Show non-USDT coins if any (e.g. XAU)
    for c in wallet.get("coins", []):
        if c["coin"] != "USDT":
            wallet_lines.append(f"{c['coin']:>9} : {c['equity']:,.4f} (Avail: {c['available']:,.4f})")
    wallet_block = "\n".join(wallet_lines)

    text = fmt(
        bold("🖥️ TRADING DESK STATUS"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
        bold("System Vitals:"), "\n", pre(sys_status), "\n\n",
        regime_banner(regime_state, hurst, adx, rec), "\n\n",
        bold("Desk Capital:"), "\n", pre(wallet_block)
    )

    from src.utils.telegram_helpers import build_nav_keyboard
    keyboard = build_nav_keyboard([
        [("🌐 Briefing", "cmd_briefing"), ("📊 Status", "cmd_status")],
        [("💼 Portfolio", "cmd_portfolio"), ("📋 Activity", "cmd_activity")],
    ])
    await _reply(update, text, reply_markup=keyboard)


async def portfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    try:
        from src.utils.telegram_helpers import format_pre_table, send_long_message, build_nav_keyboard

        bybit = _get_bybit(context)
        positions = await bybit.get_positions()

        if not positions:
            text = fmt(
                bold("💼 ACTIVE DESK PORTFOLIO"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
                italic("📭 No active positions. The desk is currently in cash.")
            )
            keyboard = build_nav_keyboard([
                [("🌐 Briefing", "cmd_briefing"), ("🔍 Scan", "cmd_scan")],
            ])
            await _reply(update, text, reply_markup=keyboard)
            return

        headers = ["Symbol", "Side", "Size", "Entry", "Mark", "uPnL"]
        rows = []
        total_pnl = 0.0
        
        detail_lines = []
        for p in positions:
            pnl = p.get("unrealized_pnl", 0.0)
            total_pnl += pnl
            emoji = "🟢" if pnl >= 0 else "🔴"
            side = p.get("side", "Buy")
            side_label = "LONG" if side == "Buy" else "SHORT"
            side_color = "🟢" if side == "Buy" else "🔴"
            size = p.get("size", 0.0)
            entry = p.get("entry_price", 0.0)
            mark = p.get("current_price", 0.0)
            pnl_pct = (pnl / (size * entry) * 100) if (size * entry) > 0 else 0.0
            
            rows.append([
                p.get("ticker", "?"),
                f"{side_label}",
                f"{size:.3f}",
                f"{entry:,.2f}",
                f"{mark:,.2f}",
                f"{emoji} ${pnl:+,.2f}"
            ])
            
            detail_lines.append(
                f"• {bold(p.get('ticker', '?'))} {side_color} {side_label} ({p.get('leverage', 1)}x)\n"
                f"  Size: {size:.4f} | Entry: ${entry:,.4f} | Mark: ${mark:,.4f}\n"
                f"  uPnL: {emoji} ${pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
            )

        table = format_pre_table(headers, rows, align_right=[2, 3, 4, 5])
        total_emoji = "🟢" if total_pnl >= 0 else "🔴"
        
        text = fmt(
            bold("💼 ACTIVE DESK PORTFOLIO"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
            pre(table), "\n\n",
            bold("Position Details:"), "\n",
            "\n".join(detail_lines), "\n",
            bold("Total Unrealized P&L:"), f" {total_emoji} ${total_pnl:+,.2f}"
        )
        keyboard = build_nav_keyboard([
            [("📊 P&L", "cmd_pnl"), ("🛡️ Risk", "cmd_risk")],
            [("📋 Activity", "cmd_activity"), ("🌐 Briefing", "cmd_briefing")],
        ])
        await send_long_message(update, str(text), reply_markup=keyboard)
    except Exception as e:
        logger.error("crypto_portfolio_failed", error=str(e))
        await _reply(update, "❌ Portfolio check failed.")


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    orchestrator = context.bot_data.get("orchestrator")
    if not orchestrator:
        await _reply(update, "⚠️ Orchestrator not connected.")
        return

    ticker = context.args[0].upper() if context.args else None

    if ticker:
        msg = await _reply(update, fmt("🔍 Scanning ", bold(ticker), "..."))
        try:
            result = await orchestrator.scan_single("CRYPTO", ticker)
            if result.get("error"):
                await msg.edit_text(str(fmt("❌ Scan failed: ", result['error'])))
                return

            conf = result.get("confidence_score", 0)
            direction = result.get("direction", "N/A")
            status = result.get("status", "SCANNED")

            from src.utils.telegram_helpers import build_nav_keyboard
            from src.utils.trader_format import signal_card
            
            if status == "EXECUTED":
                text = signal_card(
                    ticker=ticker,
                    direction=direction,
                    confidence=conf,
                    entry=result.get("fill_price") or result.get("entry_price"),
                    sl=result.get("stop_loss"),
                    tp=result.get("take_profit"),
                    reasoning=result.get("reasoning", "")
                )
            elif status == "REJECTED":
                text = fmt(
                    bold(f"⛔ RISK BLOCK: {ticker}"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
                    bold("Reason:"), f" {result.get('rejection_reason', 'Limit exceeded')}\n\n",
                    bold("Parameters:"), f" Direction: {direction} | Conviction: {conf}/100"
                )
            else:
                # Scanned but not executed (confidence < 50)
                text = fmt(
                    bold(f"🔍 SCAN DETAIL: {ticker}"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
                    bold("Direction:"), f" {direction} (Conviction: {conf}/100)\n",
                    bold("Action:"), " Standing by (requires confidence >= 50)\n\n",
                    bold("Thesis:"), "\n", italic(result.get("reasoning", "No thesis."))
                )
            
            keyboard = build_nav_keyboard([
                [("💼 Portfolio", "cmd_portfolio"), ("📋 Activity", "cmd_activity")]
            ])
            await msg.edit_text(str(text), parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            logger.error("crypto_scan_single_failed", error=str(e))
            await msg.edit_text("❌ Scan failed.")
    else:
        msg = await _reply(update, bold("🔍 Running full crypto scan..."))
        try:
            signals = await orchestrator.scan_all_markets("CRYPTO")
            
            from src.utils.telegram_helpers import format_pre_table, build_nav_keyboard
            lines = [bold(f"🔍 UNIVERSE SCAN — {len(signals)} signals"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
            
            headers = ["Ticker", "Dir", "Conf", "Status"]
            rows = []
            
            for s in signals:
                status = s.get("status", "?")
                emoji = {"EXECUTED": "✅", "REJECTED": "⛔", "EXECUTION_FAILED": "❌"}.get(status, "ℹ️")
                conf = s.get("confidence_score", 0)
                ticker = s.get("ticker", "?")
                dir_label = {"LONG": "🟢", "SHORT": "🔴"}.get(s.get("direction"), "⚪️")
                stat_str = {"EXECUTED": "EXEC", "REJECTED": "REJ", "EXECUTION_FAILED": "FAIL", "PENDING": "PEND"}.get(status, status[:4])
                
                rows.append([f"{emoji} {ticker}", dir_label, f"{conf}", stat_str])
                
            if rows:
                table = format_pre_table(headers, rows, align_right=[2])
                lines.append(pre(table))
                
                executed = [s for s in signals if s.get("status") == "EXECUTED"]
                if executed:
                    lines.append(fmt("\n", bold("✅ Executed Trades:"), "\n"))
                    for s in executed:
                        lines.append(fmt(f"• {bold(s.get('ticker'))}: {s.get('direction')} fill at ${s.get('fill_price', 'N/A'):,.4f} | Risk: ${s.get('risk_amount', 0):,.2f}\n"))
            else:
                lines.append(italic("📭 No signals generated in this pass."))
                
            keyboard = build_nav_keyboard([
                [("📋 Activity", "cmd_activity"), ("💼 Portfolio", "cmd_portfolio")]
            ])
            await msg.edit_text(str(fmt(*lines)), parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            logger.error("crypto_scan_full_failed", error=str(e))
            await msg.edit_text("❌ Scan failed.")


async def pnl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    try:
        from src.models.database import async_session
        from src.models.tables import PaperPosition, ClosedPaperTrade
        from sqlalchemy import select, func
        from src.utils.trader_format import perf_dashboard

        async with async_session() as session:
            open_result = await session.execute(select(PaperPosition).where(PaperPosition.market == "CRYPTO"))
            open_positions = open_result.scalars().all()
            
            closed_result = await session.execute(
                select(
                    func.sum(ClosedPaperTrade.realized_pnl).label("total_pnl"),
                    func.count(ClosedPaperTrade.id).label("total_trades"),
                    func.avg(ClosedPaperTrade.realized_pnl_pct).label("avg_pnl_pct")
                )
                .where(ClosedPaperTrade.market == "CRYPTO")
            )
            stats = closed_result.one()
            
            # Simple wins vs losses counting
            win_stats_result = await session.execute(
                select(
                    func.count(ClosedPaperTrade.id).label("wins"),
                    func.avg(ClosedPaperTrade.realized_pnl_pct).label("avg_win")
                )
                .where(ClosedPaperTrade.market == "CRYPTO", ClosedPaperTrade.realized_pnl_pct > 0)
            )
            win_stats = win_stats_result.one()

            loss_stats_result = await session.execute(
                select(
                    func.count(ClosedPaperTrade.id).label("losses"),
                    func.avg(ClosedPaperTrade.realized_pnl_pct).label("avg_loss")
                )
                .where(ClosedPaperTrade.market == "CRYPTO", ClosedPaperTrade.realized_pnl_pct <= 0)
            )
            loss_stats = loss_stats_result.one()

        total_pnl = stats.total_pnl or 0.0
        total_trades = stats.total_trades or 0
        wins = win_stats.wins or 0
        avg_win = win_stats.avg_win or 0.0
        avg_loss = loss_stats.avg_loss or 0.0
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

        dash = perf_dashboard(
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            total_pnl=total_pnl,
            total_trades=total_trades
        )
        
        open_pnl = sum(float(p.unrealized_pnl or 0) for p in open_positions)
        text = fmt(
            dash, "\n",
            bold("Open Risk:"), f" {len(open_positions)} active | uPnL: {'🟢' if open_pnl >= 0 else '🔴'} ${open_pnl:+,.2f}"
        )

        from src.utils.telegram_helpers import build_nav_keyboard
        keyboard = build_nav_keyboard([
            [("💰 Trades", "cmd_trades"), ("💼 Portfolio", "cmd_portfolio")],
            [("🛡️ Risk", "cmd_risk"), ("🌐 Briefing", "cmd_briefing")],
        ])
        await _reply(update, text, reply_markup=keyboard)
    except Exception as e:
        logger.error("crypto_pnl_failed", error=str(e))
        await _reply(update, "❌ P&L check failed.")


async def risk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    try:
        bybit = _get_bybit(context)
        positions = await bybit.get_positions()
        wallet = await bybit.get_wallet_balance()
        balance = wallet.get("balance", 0)

        position_count = len(positions)
        r = _get_redis(context)
        cooldown = await r.get("karsa:crypto_cooldown")

        risk_block = (
            f"Max Risk/Trade : {settings.CRYPTO_MAX_RISK_PER_TRADE_PCT}%\n"
            f"Max Position   : {settings.CRYPTO_MAX_POSITION_PCT}%\n"
            f"Max Concurrent : {settings.CRYPTO_MAX_CONCURRENT_POSITIONS}\n"
            f"Daily Loss Cap : {settings.CRYPTO_DAILY_LOSS_LIMIT_PCT}%\n"
            f"Open Positions : {position_count}/{settings.CRYPTO_MAX_CONCURRENT_POSITIONS}\n"
            f"Cooldown State : {'⏳ Cooldown Active' if cooldown else '🟢 Clear'}"
        )

        margin_used = wallet.get("used_margin", 0)
        margin_pct = (margin_used / balance * 100) if balance > 0 else 0
        
        # Format a margin usage bar
        filled_margin = min(10, int(margin_pct / 10))
        margin_bar = "█" * filled_margin + "░" * (10 - filled_margin)

        margin_block = (
            f"Balance   : ${balance:,.2f}\n"
            f"Used Margin: ${margin_used:,.2f} ({margin_pct:.1f}%)\n"
            f"Usage Bar : {margin_bar}\n"
            f"Available : ${wallet.get('available', 0):,.2f}"
        )

        from src.utils.telegram_helpers import build_nav_keyboard
        text = fmt(
            bold("🛡️ RISK MANAGEMENT DESK"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n", 
            bold("Risk Configuration:"), "\n", pre(risk_block), "\n\n", 
            bold("Capital Allocation:"), "\n", pre(margin_block)
        )
        keyboard = build_nav_keyboard([
            [("💼 Portfolio", "cmd_portfolio"), ("📊 P&L", "cmd_pnl")],
        ])
        await _reply(update, text, reply_markup=keyboard)
    except Exception as e:
        logger.error("crypto_risk_cmd_failed", error=str(e))
        await _reply(update, "❌ Risk check failed.")


async def kill_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    operator = update.effective_user.username or str(update.effective_user.id)
    try:
        from src.risk.emergency import activate_global_halt
        from src.risk.sor import SmartOrderRouter

        bybit = _get_bybit(context)
        sor = SmartOrderRouter(bybit)
        flatten_result = await sor.flatten_all()

        await activate_global_halt(reason=f"Manual kill by {operator}", operator=operator)

        await _reply(update, fmt(
            "🚨 ", bold("EMERGENCY KILL"), "\n\n",
            f"Positions closed: {flatten_result.get('count', 0)}\n",
            "Global halt: ACTIVE\n\n",
            "All trading halted. Use ", code("/resume"), " to reactivate."
        ))
    except Exception as e:
        logger.error("crypto_kill_failed", error=str(e))
        await _reply(update, "❌ Kill command failed.")


async def sellall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    try:
        from src.risk.sor import SmartOrderRouter

        bybit = _get_bybit(context)
        sor = SmartOrderRouter(bybit)
        flatten_result = await sor.flatten_all()

        r = _get_redis(context)
        await r.set("karsa:crypto_cooldown", "1", ex=900)

        orchestrator = context.bot_data.get("orchestrator")
        if orchestrator and hasattr(orchestrator, "crypto_agent"):
            orchestrator.crypto_agent.wipe_memory()

        await _reply(update, fmt(
            "🧹 ", bold("SELL ALL"), "\n\n",
            f"Positions closed: {flatten_result.get('count', 0)}\n",
            "Memory wiped: ✅\n", "Cooldown: 15 minutes\n\n",
            "No new trades for 15 minutes."
        ))
    except Exception as e:
        logger.error("crypto_sellall_failed", error=str(e))
        await _reply(update, "❌ Sell all failed.")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    operator = update.effective_user.username or str(update.effective_user.id)
    try:
        from src.risk.emergency import deactivate_global_halt
        await deactivate_global_halt(operator=operator)

        r = _get_redis(context)
        await r.delete("karsa:crypto_cooldown")

        await _reply(update, fmt("✅ ", bold("Trading resumed."), "\n", "Global halt cleared. Cooldown cleared."))
    except Exception as e:
        logger.error("crypto_resume_failed", error=str(e))
        await _reply(update, "❌ Resume failed.")


async def _execute_pending_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute all PENDING crypto signals via orchestrator's auto-execute path."""
    if not _is_authorized(update):
        return
    try:
        from src.models.database import async_session
        from src.models.tables import Signal
        from sqlalchemy import select

        async with async_session() as session:
            result = await session.execute(
                select(Signal).where(Signal.market == "CRYPTO", Signal.status == "PENDING")
                .order_by(Signal.confidence_score.desc())
            )
            pending = result.scalars().all()

        if not pending:
            await _reply(update, "📭 No pending signals to execute.")
            return

        orchestrator = context.bot_data.get("orchestrator")
        if not orchestrator:
            await _reply(update, "⚠️ Orchestrator not connected.")
            return

        msg = await _reply(update, fmt("⚡ Executing ", bold(str(len(pending))), " pending signals..."))

        from src.advisory.crypto_regime import CryptoRegimeFilter
        regime_filter = CryptoRegimeFilter(orchestrator.mcp)
        regime = await regime_filter.get_current_regime()

        # Build signal dicts from DB objects
        signal_dicts = [{
            "ticker": s.ticker, "market": "CRYPTO", "direction": s.direction,
            "confidence_score": s.confidence_score, "entry_price": float(s.entry_price or 0),
            "target_price": float(s.target_price or 0) if s.target_price else None,
            "stop_loss_price": float(s.stop_loss_price or 0) if s.stop_loss_price else None,
            "reasoning": s.reasoning, "strategy": s.strategy,
        } for s in pending]

        executed = await orchestrator._auto_execute_crypto(signal_dicts, regime)

        results = []
        for sig in executed:
            status = sig.get("status", "?")
            emoji = {"EXECUTED": "✅", "REJECTED": "⛔", "EXECUTION_FAILED": "❌", "HALTED": "🚨"}.get(status, "ℹ️")
            conf = sig.get("confidence_score", 0)
            line = fmt(f"{emoji} ", bold(sig.get("ticker", "?")), f" — {sig.get('direction', '?')} ({conf}%) — {status}")
            if status == "EXECUTED":
                line = fmt(line, f"\n   Fill: {sig.get('fill_price', 'N/A')} | Risk: ${sig.get('risk_amount', 0):,.2f}")
            elif status == "REJECTED":
                line = fmt(line, f"\n   Reason: {sig.get('rejection_reason', 'Unknown')}")
            results.append(str(line))

        text = fmt(bold("⚡ EXECUTE RESULTS"), "\n\n", "\n".join(results))
        await msg.edit_text(str(text), parse_mode="HTML")

    except Exception as e:
        logger.error("execute_all_failed", error=str(e))
        await _reply(update, "❌ Execute all failed.")


async def _execute_single_signal(update: Update, context: ContextTypes.DEFAULT_TYPE, signal_id: str):
    """Execute a single PENDING signal by ID."""
    if not _is_authorized(update):
        return
    try:
        from src.models.database import async_session
        from src.models.tables import Signal
        from sqlalchemy import select
        import uuid

        async with async_session() as session:
            result = await session.execute(
                select(Signal).where(Signal.id == uuid.UUID(signal_id), Signal.status == "PENDING")
            )
            signal = result.scalar_one_or_none()

        if not signal:
            await update.callback_query.answer("Signal not found or already processed.", show_alert=True)
            return

        orchestrator = context.bot_data.get("orchestrator")
        if not orchestrator:
            await _reply(update, "⚠️ Orchestrator not connected.")
            return

        from src.advisory.crypto_regime import CryptoRegimeFilter
        regime_filter = CryptoRegimeFilter(orchestrator.mcp)
        regime = await regime_filter.get_current_regime()

        signal_dict = {
            "ticker": signal.ticker, "market": "CRYPTO", "direction": signal.direction,
            "confidence_score": signal.confidence_score,
            "entry_price": float(signal.entry_price or 0),
            "target_price": float(signal.target_price or 0) if signal.target_price else None,
            "stop_loss_price": float(signal.stop_loss_price or 0) if signal.stop_loss_price else None,
            "reasoning": signal.reasoning, "strategy": signal.strategy,
        }
        executed = await orchestrator._auto_execute_crypto([signal_dict], regime)
        result = executed[0] if executed else signal_dict
        status = result.get("status", "SCANNED")
        emoji = {"EXECUTED": "✅", "REJECTED": "⛔", "EXECUTION_FAILED": "❌"}.get(status, "ℹ️")
        conf = result.get("confidence_score", 0)
        text = fmt(
            emoji, " ", bold(signal.ticker), "\n",
            bold("Direction:"), f" {result.get('direction', '?')}\n",
            bold("Confidence:"), f" {conf}%\n",
            bold("Status:"), f" {status}",
        )
        if status == "EXECUTED":
            text = fmt(text, "\n", bold("Fill:"), f" {result.get('fill_price', 'N/A')}")
        elif status == "REJECTED":
            text = fmt(text, "\n", bold("Reason:"), f" {result.get('rejection_reason', 'Unknown')}")

        await update.callback_query.edit_message_text(str(text), parse_mode="HTML")

    except Exception as e:
        logger.error("execute_single_failed", error=str(e), signal_id=signal_id)
        await _reply(update, "❌ Execute failed.")


async def _dismiss_signal(update: Update, signal_id: str):
    """Dismiss (reject) a single PENDING signal."""
    if not _is_authorized(update):
        return
    try:
        from src.models.database import async_session
        from src.models.tables import Signal
        from sqlalchemy import select
        import uuid

        async with async_session() as session:
            result = await session.execute(
                select(Signal).where(Signal.id == uuid.UUID(signal_id), Signal.status == "PENDING")
            )
            signal = result.scalar_one_or_none()

            if not signal:
                await update.callback_query.answer("Already processed.", show_alert=True)
                return

            signal.status = "REJECTED"
            await session.commit()

        await update.callback_query.edit_message_text(
            str(fmt("⛔ ", bold(f"Dismissed: {signal.ticker}"), "\nSignal discarded.")),
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error("dismiss_signal_failed", error=str(e), signal_id=signal_id)
        await _reply(update, "❌ Dismiss failed.")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "cmd_portfolio":
        await portfolio_cmd(update, context)
    elif data == "cmd_pnl":
        await pnl_cmd(update, context)
    elif data == "cmd_risk":
        await risk_cmd(update, context)
    elif data == "cmd_status":
        await status_cmd(update, context)
    elif data == "cmd_activity":
        await activity_cmd(update, context)
    elif data == "cmd_audit_agent":
        await audit_agent_cmd(update, context)
    elif data == "crypto_exec_all":
        await _execute_pending_signals(update, context)
    elif data.startswith("crypto_exec_"):
        signal_id = data.replace("crypto_exec_", "")
        await _execute_single_signal(update, context, signal_id)
    elif data.startswith("crypto_dismiss_"):
        signal_id = data.replace("crypto_dismiss_", "")
        await _dismiss_signal(update, signal_id)
    elif data == "cmd_guide":
        await guide_cmd(update, context)
    elif data == "cmd_regime":
        await regime_cmd(update, context)
    elif data == "cmd_funding":
        await funding_cmd(update, context)
    elif data == "cmd_trades":
        await trades_cmd(update, context)
    elif data == "cmd_scan":
        await scan_cmd(update, context)
    elif data == "cmd_briefing":
        await briefing_cmd(update, context)


async def activity_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent agent activity — what was scanned, executed, rejected."""
    if not _is_authorized(update):
        return
    try:
        from src.models.database import async_session
        from src.models.tables import Signal, ClosedPaperTrade
        from sqlalchemy import select, desc
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        async with async_session() as session:
            sig_result = await session.execute(
                select(Signal).where(Signal.market == "CRYPTO")
                .order_by(desc(Signal.created_at)).limit(10)
            )
            signals = sig_result.scalars().all()

            closed_result = await session.execute(
                select(ClosedPaperTrade).where(ClosedPaperTrade.market == "CRYPTO")
                .order_by(desc(ClosedPaperTrade.exit_date)).limit(10)
            )
            closed = closed_result.scalars().all()

        lines = [bold("📋 CRYPTO ACTIVITY"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"]

        # --- Signals section ---
        pending_count = 0
        if signals:
            lines.append(fmt(bold("🔍 SIGNALS"), "\n\n"))

            status_label = {
                "EXECUTED": "✅ Executed",
                "REJECTED": "⛔ Rejected",
                "PENDING": "⏳ Pending",
                "EXECUTION_FAILED": "❌ Failed",
                "HALTED": "🚨 Halted",
            }
            direction_label = {"LONG": "🟢 Long", "SHORT": "🔴 Short", "CLOSE": "⚪ Close"}

            for s in signals:
                ts = s.created_at.strftime("%b %d, %H:%M") if s.created_at else "?"
                conf = s.confidence_score or 0
                ticker = s.ticker or "?"
                direction = direction_label.get(s.direction or "", s.direction or "?")
                status = status_label.get(s.status, s.status or "?")
                
                full_reason = (s.reasoning or "").strip()
                reasoning = full_reason[:75] + "..." if len(full_reason) > 75 else full_reason

                lines.append(fmt(bold(ticker), f"  {direction}  ", code(f" {conf}% "), "\n"))
                lines.append(fmt("├ ", status, f"  •  {ts}\n"))
                if reasoning:
                    lines.append(fmt("└ ", italic(reasoning), "\n\n"))
                else:
                    lines.append(fmt("└ ", italic("No reasoning provided."), "\n\n"))
                
                if s.status == "PENDING":
                    pending_count += 1

        # --- Closed trades section ---
        if closed:
            lines.append(fmt(bold("💰 CLOSED TRADES"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"))

            for t in closed:
                pnl = t.realized_pnl_pct or 0
                emoji = "🟢" if pnl > 0 else "🔴"
                ts = t.exit_date.strftime("%b %d, %H:%M") if t.exit_date else "?"
                side = "Long" if t.side == "Buy" else "Short"
                ticker = t.ticker or "?"
                reason = t.exit_reason or "N/A"

                lines.append(fmt(bold(ticker), f"  {side}\n"))
                lines.append(fmt("├ ", emoji, f" {pnl:+.1f}%\n"))
                lines.append(fmt("└ ", italic(f"{reason}  •  {ts}"), "\n\n"))

        if not signals and not closed:
            lines.append(fmt(italic("📭 No activity yet. Signals will appear here after scans."), "\n"))

        # Build keyboard: nav buttons + execute-all if pending signals exist
        nav_rows = [
            [("📊 P&L", "cmd_pnl"), ("🛡️ Risk", "cmd_risk")],
            [("💼 Portfolio", "cmd_portfolio")],
        ]
        if pending_count > 0:
            nav_rows.insert(0, [(f"✅ Execute All Pending ({pending_count})", "crypto_exec_all")])
        from src.utils.telegram_helpers import send_long_message, build_nav_keyboard
        keyboard = build_nav_keyboard(nav_rows)
        await send_long_message(update, str(fmt(*lines)), reply_markup=keyboard)

    except Exception as e:
        logger.error("activity_cmd_failed", error=str(e))
        await _reply(update, "❌ Activity log failed.")


async def audit_agent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run crypto agent audit — performance review + improvement recommendations."""
    if not _is_authorized(update):
        return
    try:
        from src.advisory.crypto_audit import CryptoAuditMetrics
        from src.agents.crypto_auditor import CryptoAuditorAgent
        from src.utils.telegram_helpers import send_long_message, build_nav_keyboard

        msg = await _reply(update, fmt("🔍 ", bold("Running agent audit...")))

        # Step 1: Gather deterministic metrics
        metrics_engine = CryptoAuditMetrics()
        metrics = await metrics_engine.gather(days=7)

        # Step 2: Format metrics block
        total = metrics["total_trades"]
        win_rate = metrics["win_rate"]
        avg_win = metrics["avg_win_pct"]
        avg_loss = metrics["avg_loss_pct"]
        total_pnl = metrics["total_pnl_usd"]
        sig = metrics["signals"]

        perf_block = (
            f"Trades   : {total} closed  |  Win Rate: {win_rate}%\n"
            f"Avg Win  : +{avg_win}%      |  Avg Loss: {avg_loss}%\n"
            f"Total P&L: ${total_pnl:+,.2f}\n"
        )
        if metrics["best_trade"]:
            b = metrics["best_trade"]
            perf_block += f"Best     : {b['ticker']} {b['pnl_pct']:+.1f}%\n"
        if metrics["worst_trade"]:
            w = metrics["worst_trade"]
            perf_block += f"Worst    : {w['ticker']} {w['pnl_pct']:+.1f}%\n"

        sig_block = (
            f"Total    : {sig['total']} signals\n"
            f"Executed : {sig['executed']}  |  Rejected: {sig['rejected']}  |  Pending: {sig['pending']}\n"
            f"Avg Conf : {sig['avg_confidence']}%"
        )

        # By ticker table
        ticker_lines = []
        for tk, data in sorted(metrics["by_ticker"].items(), key=lambda x: x[1]["pnl_usd"], reverse=True):
            tk_total = data["wins"] + data["losses"]
            tk_wr = (data["wins"] / tk_total * 100) if tk_total else 0
            emoji = "🟢" if data["pnl_usd"] >= 0 else "🔴"
            ticker_lines.append(f"  {tk:<10} {tk_total} trades  |  {tk_wr:.0f}% win  |  {emoji} ${data['pnl_usd']:+,.2f}")

        # By direction
        dir_lines = []
        for d, data in metrics["by_direction"].items():
            d_wr = (data["wins"] / data["count"] * 100) if data["count"] else 0
            dir_lines.append(f"  {d:<6} {data['count']} trades  |  {d_wr:.0f}% win")

        report_lines = [
            bold("🔍 AGENT AUDIT — 7 Day Review"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
            bold("Performance:"), "\n", pre(perf_block), "\n",
            bold("Signals:"), "\n", pre(sig_block), "\n",
        ]
        if ticker_lines:
            report_lines.extend([bold("By Ticker:"), "\n", pre("\n".join(ticker_lines)), "\n"])
        if dir_lines:
            report_lines.extend([bold("By Direction:"), "\n", pre("\n".join(dir_lines)), "\n"])

        # Step 3: LLM recommendations (only if enough data)
        if total >= 2:
            report_lines.extend([bold("💡 Recommendations:"), "\n"])
            try:
                orch = context.bot_data.get("orchestrator")
                if orch:
                    auditor = CryptoAuditorAgent(orch.mcp)
                    analysis = await auditor.run_audit(metrics)

                    grade = analysis.get("grade", "?")
                    summary = analysis.get("summary", "No summary")
                    report_lines.append(fmt(code(f"Grade: {grade}"), f" — {summary}\n"))

                    for rec in analysis.get("recommendations", []):
                        report_lines.append(fmt("• ", rec, "\n"))

                    if analysis.get("confidence_note"):
                        report_lines.append(fmt("\n", bold("Confidence:"), f" {analysis['confidence_note']}"))
                else:
                    report_lines.append(italic("Orchestrator not connected — skipping LLM analysis.\n"))
            except Exception as e:
                logger.error("audit_llm_failed", error=str(e))
                report_lines.append(italic("LLM analysis unavailable — showing metrics only.\n"))
        else:
            report_lines.extend([
                italic("Not enough closed trades for LLM analysis (need 2+)."), "\n",
                italic("Keep trading — audit recommendations will appear after more data."),
            ])

        keyboard = build_nav_keyboard([
            [("📋 Activity", "cmd_activity"), ("📊 P&L", "cmd_pnl")],
            [("💼 Portfolio", "cmd_portfolio")],
        ])
        await msg.edit_text(str(fmt(*report_lines)), parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logger.error("audit_agent_cmd_failed", error=str(e))
        await _reply(update, "❌ Audit failed.")


# --- Phase 5 UX: New Commands ---

async def guide_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Full crypto trading walkthrough."""
    if not _is_authorized(update):
        return
    from src.utils.telegram_helpers import build_nav_keyboard

    guide_text = fmt(
        bold("📖 KARSA CRYPTO 101"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
        bold("🤖 What is Karsa Crypto?"), "\n",
        "AI-powered perpetual futures on Bybit.\n",
        "EXECUTES trades automatically after AI analysis.\n\n",
        bold("⚡ HOW IT WORKS"), "\n",
        "🔍 Step 1: AI scans 10 perpetual pairs (every 4h)\n",
        "🛡️ Step 2: Risk gates (1% risk, 3% daily loss, 5 max positions)\n",
        "📈 Step 3: Smart execution (limit → reprice → market fallback)\n\n",
        bold("🌡️ REGIME"), "\n",
        "  🟢 TREND_BULL → full sizing (1.2x)\n",
        "  🔴 TREND_BEAR → reduced (0.5x)\n",
        "  🟡 MEAN_REVERSION → moderate (0.8x)\n",
        "  ⚪ CHOP → minimal (0.5x)\n\n",
        bold("🚨 EMERGENCY"), "\n",
        "  ", code("/kill"), " Close ALL + halt\n",
        "  ", code("/sellall"), " Close + 15min cooldown\n",
        "  ", code("/resume"), " Reactivate\n\n",
        bold("📋 COMMANDS"), "\n",
        code("/status"), " ", code("/portfolio"), " ", code("/pnl"), " ", code("/risk"), "\n",
        code("/scan"), " ", code("/activity"), " ", code("/regime"), " ", code("/funding"), "\n",
    )
    keyboard = build_nav_keyboard([
        [("📊 Status", "cmd_status"), ("💼 Portfolio", "cmd_portfolio")],
        [("🛡️ Risk", "cmd_risk"), ("📋 Activity", "cmd_activity")],
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text(str(guide_text), parse_mode="HTML", reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(str(guide_text), parse_mode="HTML", reply_markup=keyboard)


async def regime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    try:
        from src.advisory.crypto_regime import CryptoRegimeFilter
        from src.utils.trader_format import regime_banner

        orch = context.bot_data.get("orchestrator")
        regime = await CryptoRegimeFilter(orch.mcp).get_current_regime()

        state = regime.get("state", "UNKNOWN")
        hurst = regime.get("hurst", 0.5)
        adx = regime.get("adx", 0.0)
        rec = regime.get("recommendation", "")
        btc_price = regime.get("benchmark_price", "N/A")
        btc_dom = regime.get("btc_dominance")
        season = regime.get("market_season", "UNKNOWN")

        dom_block = ""
        if btc_dom is not None:
            season_e = {"BTC_SEASON": "₿", "ALT_SEASON": "🪙", "NEUTRAL": "⚖️"}.get(season, "❓")
            dom_block = f"\nBTC Dominance: {btc_dom}% | {season_e} {season}"

        text = fmt(
            bold("🌡️ MARKET REGIME ANALYSIS"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
            regime_banner(state, hurst, adx, rec),
            pre(f"Benchmark Symbol: BTCUSDT\nBenchmark Price : ${btc_price:,.2f}" if isinstance(btc_price, (int, float)) else f"Benchmark Symbol: BTCUSDT\nBenchmark Price : {btc_price}" + dom_block)
        )
        
        from src.utils.telegram_helpers import build_nav_keyboard
        keyboard = build_nav_keyboard([
            [("📊 Status", "cmd_status"), ("🔍 Scan", "cmd_scan")],
            [("💼 Portfolio", "cmd_portfolio")]
        ])
        await _reply(update, text, reply_markup=keyboard)
    except Exception as e:
        logger.error("regime_cmd_failed", error=str(e))
        await _reply(update, "❌ Regime check failed.")


async def funding_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    try:
        from src.risk.funding_tracker import FundingTracker
        from src.utils.telegram_helpers import send_long_message, format_pre_table, build_nav_keyboard
        from src.utils.trader_format import funding_gauge

        bybit = _get_bybit(context)
        tracker = FundingTracker(bybit)
        rates = await tracker.get_current_rates()

        lines = [bold("📊 FUNDING RATES & CROWDING"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"]
        
        headers = ["Symbol", "Rate", "Annualized", "Direction"]
        rows = []
        gauge_lines = []
        
        for ri in sorted(rates, key=lambda x: abs(x.get("funding_rate", 0)), reverse=True):
            rate = ri.get("funding_rate", 0)
            annual = ri.get("annualized_pct", 0)
            d = "L→S (Crowded Long)" if rate > 0 else "S→L (Crowded Short)" if rate < 0 else "—"
            alert = " ⚠️" if ri.get("alert") else ""
            
            rows.append([
                ri['symbol'],
                f"{rate*100:+.4f}%",
                f"{annual:+.0f}%",
                f"{d}{alert}"
            ])
            
            gauge_lines.append(
                f"• {bold(ri['symbol'])}: " + str(funding_gauge(rate)) + f" (Annual: {annual:+.0f}%)\n"
            )
            
        table = format_pre_table(headers, rows, align_right=[1, 2])
        lines.append(pre(table))
        lines.append("\n" + bold("Heatmap:") + "\n")
        lines.extend(gauge_lines)
        lines.append("\n💡 L→S = Longs pay Shorts. S→L = Shorts pay Longs. High rates indicate potential reversal squeeze points.\n")
        
        keyboard = build_nav_keyboard([
            [("📊 Status", "cmd_status"), ("🛡️ Risk", "cmd_risk")]
        ])
        await send_long_message(update, str(fmt(*lines)), reply_markup=keyboard)
    except Exception as e:
        logger.error("funding_cmd_failed", error=str(e))
        await _reply(update, "❌ Funding check failed.")


async def trades_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    try:
        from src.models.database import async_session
        from src.models.tables import ClosedPaperTrade
        from sqlalchemy import select, desc
        from src.utils.telegram_helpers import send_long_message

        async with async_session() as session:
            result = await session.execute(
                select(ClosedPaperTrade).where(ClosedPaperTrade.market == "CRYPTO")
                .order_by(desc(ClosedPaperTrade.exit_date)).limit(20)
            )
            trades = result.scalars().all()

        if not trades:
            await _reply(update, italic("📭 No closed crypto trades yet."))
            return

        from src.utils.telegram_helpers import format_pre_table, build_nav_keyboard
        
        lines = [bold("📊 CLOSED DESK JOURNAL (Last 20)"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"]
        
        headers = ["Ticker", "Side", "P&L", "Return"]
        rows = []
        total_pnl, wins = 0, 0
        for t in trades:
            pnl = float(t.realized_pnl or 0)
            total_pnl += pnl
            if pnl >= 0: wins += 1
            e = "🟢" if pnl >= 0 else "🔴"
            ret = float(t.realized_pnl_pct or 0)
            rows.append([
                t.ticker,
                t.side or '?',
                f"{e} ${pnl:+,.2f}",
                f"{ret:+.1f}%"
            ])

        table = format_pre_table(headers, rows, align_right=[2, 3])
        lines.append(pre(table))

        wr = (wins / len(trades) * 100) if trades else 0
        te = "🟢" if total_pnl >= 0 else "🔴"
        
        # Formatted win rate bar
        wr_filled = min(10, int(wr / 10))
        wr_bar = "█" * wr_filled + "░" * (10 - wr_filled)
        
        summary_block = (
            f"Total Operations : {len(trades)}\n"
            f"Win Rate         : {wr_bar} {wr:.1f}%\n"
            f"Realized Return  : {te} ${total_pnl:+,.2f}"
        )
        lines.append(fmt("\n", bold("Journal Summary:"), "\n", pre(summary_block)))
        
        keyboard = build_nav_keyboard([
            [("📊 P&L", "cmd_pnl"), ("📋 Activity", "cmd_activity")]
        ])
        await send_long_message(update, str(fmt(*lines)), reply_markup=keyboard)
    except Exception as e:
        logger.error("trades_cmd_failed", error=str(e))
        await _reply(update, "❌ Trades check failed.")


async def briefing_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Daily crypto market brief in trader voice."""
    if not _is_authorized(update):
        return
    
    orch = context.bot_data.get("orchestrator")
    if not orch:
        await _reply(update, "⚠️ Orchestrator not connected.")
        return

    msg = await _reply(update, fmt("🌐 ", bold("Assembling trading desk briefing...")))
    try:
        from src.advisory.crypto_regime import CryptoRegimeFilter
        from src.advisory.crypto_market_watch import CryptoMarketWatchEngine
        from src.utils.trader_format import briefing_block

        # 1. Fetch regime
        regime_filter = CryptoRegimeFilter(orch.mcp)
        regime = await regime_filter.get_current_regime()

        # 2. Fetch briefing data
        data = await CryptoMarketWatchEngine.get_briefing_data(orch.mcp, regime)

        # 3. Format output
        text = briefing_block(
            regime=data["regime"],
            top_movers=data["top_movers"],
            funding_alerts=data["funding_alerts"]
        )

        from src.utils.telegram_helpers import build_nav_keyboard
        keyboard = build_nav_keyboard([
            [("🔍 Scan Universe", "cmd_scan"), ("💼 Portfolio", "cmd_portfolio")],
            [("📊 Status", "cmd_status"), ("📋 Activity", "cmd_activity")]
        ])
        
        if update.callback_query:
            await update.callback_query.message.edit_text(str(text), parse_mode="HTML", reply_markup=keyboard)
        else:
            await msg.edit_text(str(text), parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logger.error("briefing_cmd_failed", error=str(e))
        await msg.edit_text("❌ Failed to assemble market briefing.")


async def market_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Coin deep-dive technical + sentiment snapshot."""
    if not _is_authorized(update):
        return
    
    orch = context.bot_data.get("orchestrator")
    if not orch:
        await _reply(update, "⚠️ Orchestrator not connected.")
        return

    ticker = context.args[0].upper() if context.args else None
    if not ticker:
        await _reply(update, "💡 Usage: /market <coin> (e.g. /market BTCUSDT)")
        return

    # Normalize symbol
    if not ticker.endswith("USDT"):
        ticker += "USDT"

    msg = await _reply(update, fmt("🖥️ ", bold(f"Compiling snapshot for {ticker}...")))
    try:
        from src.advisory.crypto_market_watch import CryptoMarketWatchEngine
        from src.utils.trader_format import market_snapshot_card

        snapshot = await CryptoMarketWatchEngine.get_market_snapshot(orch.mcp, ticker)
        if snapshot.get("error"):
            await msg.edit_text(f"❌ Failed to fetch data: {snapshot['error']}")
            return

        text = market_snapshot_card(
            ticker=ticker,
            quote=snapshot["quote"],
            ta=snapshot["ta"],
            funding=snapshot["funding"],
            oi=snapshot["open_interest"]
        )

        from src.utils.telegram_helpers import build_nav_keyboard
        keyboard = build_nav_keyboard([
            [("💼 Portfolio", "cmd_portfolio")],
            [("🌐 Briefing", "cmd_briefing")]
        ])
        
        await msg.edit_text(str(text), parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logger.error("market_cmd_failed", ticker=ticker, error=str(e))
        await msg.edit_text("❌ Failed to compile snapshot.")
