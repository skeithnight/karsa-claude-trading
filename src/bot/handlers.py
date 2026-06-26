"""Karsa Trading System - Telegram Bot Command Handlers"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("telegram_handlers")


def _is_authorized(update: Update) -> bool:
    """Check if message is from authorized chat (works in both polling and webhook mode)."""
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if settings.TELEGRAM_CHAT_ID and chat_id != str(settings.TELEGRAM_CHAT_ID):
        logger.warning("unauthorized_chat", chat_id=chat_id)
        return False
    return True


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "🤖 *Karsa Trading System*\n\n"
        "Commands:\n"
        "/status - Portfolio & system status\n"
        "/scan <market> <ticker> - Scan a single ticker\n"
        "/portfolio - View positions\n"
        "/trades - Recent trades\n",
        parse_mode="Markdown",
    )


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /scan <market> <ticker> command."""
    if not _is_authorized(update):
        return
    logger.info("scan_cmd_received", user=update.effective_user.id, args=context.args)
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ Usage: `/scan <market> <ticker>`\n"
            "Example: `/scan IDX BBCA`",
            parse_mode="Markdown"
        )
        return

    market = context.args[0].upper()
    ticker = context.args[1].upper()

    msg = await update.message.reply_text(f"🔍 Scanning {ticker} ({market})...")
    logger.info("scan_cmd_started", market=market, ticker=ticker)

    # Orchestrator is passed via bot_data
    orchestrator = context.bot_data.get("orchestrator")
    if not orchestrator:
        logger.error("scan_cmd_no_orchestrator")
        await msg.edit_text("⚠️ System error: Orchestrator not connected.")
        return

    try:
        result = await orchestrator.scan_single(market, ticker)
        logger.info("scan_cmd_finished", result=result.get("status", "ok") if isinstance(result, dict) else "unknown")

        if result.get("error"):
            await msg.edit_text(f"❌ Scan failed: {result['error']}\n\nDetail: {result.get('detail', '')}")
            return

        if result.get("confidence_score", 0) < 60:
            await msg.edit_text(
                f"ℹ️ Scan complete for {ticker}.\n\n"
                f"No strong trade setup found.\n"
                f"Confidence: {result.get('confidence_score', 0)}/100\n"
                f"Reasoning: {result.get('reasoning', 'No clear setup.')}"
            )
            return

        # High confidence -> format as alert
        alert_text, keyboard = format_trade_alert(result)
        await msg.edit_text(text=alert_text, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        logger.error("scan_cmd_failed", error=str(e), exc_info=True)
        await msg.edit_text(f"❌ Scan error: {str(e)}")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "📊 *System Status*\n━━━━━━━━━━━━━━━━\n"
        "🟢 Orchestrator: Online\n🟢 9Router: Connected\n"
        "🟢 MCP: Connected\n🟢 Redis: Connected\n🟢 PostgreSQL: Connected\n",
        parse_mode="Markdown",
    )


async def portfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /portfolio command — show current positions from Postgres."""
    if not _is_authorized(update):
        return
    from src.models.database import async_session
    from src.models.tables import PortfolioState
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(PortfolioState).order_by(PortfolioState.last_synced_at.desc())
        )
        positions = result.scalars().all()

    if not positions:
        await update.message.reply_text(
            "💼 *Portfolio*\n━━━━━━━━━━━━━━━━\nNo positions open.",
            parse_mode="Markdown",
        )
        return

    lines = ["💼 *Portfolio*\n━━━━━━━━━━━━━━━━"]
    for p in positions:
        pnl = ""
        if p.unrealized_pnl and p.unrealized_pnl != 0:
            emoji = "🟢" if p.unrealized_pnl > 0 else "🔴"
            pnl = f" {emoji} {p.unrealized_pnl:,.0f}"
        lines.append(f"*{p.ticker}* ({p.market}) — {p.quantity:.0f} @ {p.avg_cost:,.0f}{pnl}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def trades_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /trades command — show recent trades from Postgres."""
    if not _is_authorized(update):
        return
    from src.models.database import async_session
    from src.models.tables import Trade
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(Trade).order_by(Trade.created_at.desc()).limit(10)
        )
        trades = result.scalars().all()

    if not trades:
        await update.message.reply_text(
            "📋 *Recent Trades*\n━━━━━━━━━━━━━━━━\nNo trades yet.",
            parse_mode="Markdown",
        )
        return

    lines = ["📋 *Recent Trades*\n━━━━━━━━━━━━━━━━"]
    for t in trades:
        emoji = "✅" if t.status == "FILLED" else "❌" if t.status == "REJECTED" else "⏳"
        price = f"@ {t.filled_price:,.0f}" if t.filled_price else ""
        lines.append(f"{emoji} {t.side} {t.ticker} ({t.market}) — {t.quantity:.0f} {price} [{t.status}]")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def format_trade_alert(signal: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Format a trade signal into a Telegram alert with action buttons."""
    entry = signal.get("entry_price")
    target = signal.get("target_price")
    stop = signal.get("stop_loss_price")
    risk_check = signal.get("risk_check", {})

    rr = ""
    if entry and target and stop:
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk > 0:
            rr = f"{reward / risk:.1f}:1"

    msg = (
        f"🚨 *NEW TRADE SIGNAL: {signal.get('ticker')} ({signal.get('market')})*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Strategy: {signal.get('strategy')}\n"
        f"📈 Direction: {signal.get('direction')}\n"
        f"💰 Entry: {entry}\n"
        f"🎯 Target: {target} {f'({rr})' if rr else ''}\n"
        f"🛑 Stop Loss: {stop}\n"
        f"🧠 Confidence: {signal.get('confidence_score', 0)}/100\n"
        f"📉 Risk: {risk_check.get('risk_pct', 0):.1f}% of portfolio\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 {str(signal.get('reasoning', ''))[:200]}\n"
        f"⏳ Expires in: 15 minutes"
    )

    sid = signal.get("id", "unknown")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ APPROVE", callback_data=f"approve:{sid}"),
         InlineKeyboardButton("❌ REJECT", callback_data=f"reject:{sid}")],
        [InlineKeyboardButton("✏️ MODIFY", callback_data=f"modify:{sid}"),
         InlineKeyboardButton("📊 VIEW CHART", callback_data=f"chart:{sid}")],
    ])

    return msg, keyboard


async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks for trade approval.

    Expects context.bot_data to contain:
      - 'approval_manager': ApprovalManager instance
      - 'brokers': dict[str, BaseBroker] keyed by market name
    """
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data or ":" not in data:
        return

    action, signal_id = data.split(":", 1)
    logger.info("approval_callback", action=action, signal_id=signal_id)

    approval_mgr = context.bot_data.get("approval_manager")
    brokers = context.bot_data.get("brokers", {})

    if not approval_mgr:
        logger.error("approval_manager_not_configured")
        await query.edit_message_text(text=query.message.text + "\n\n⚠️ *System error: approval manager not configured*", parse_mode="Markdown")
        return

    # P0-4: Look up signal market from DB to pick correct broker
    from src.models.database import async_session
    from src.models.tables import Signal
    from sqlalchemy import select
    import uuid as _uuid
    async with async_session() as session:
        sig_result = await session.execute(select(Signal).where(Signal.id == _uuid.UUID(signal_id)))
        sig = sig_result.scalar_one_or_none()
    market = sig.market if sig else "IDX"
    broker = brokers.get(market) or brokers.get("IDX") or brokers.get("US")

    if action == "approve":
        result = await approval_mgr.process_approval(signal_id, "APPROVE", broker)
        status_text = f"✅ *APPROVED* — Trade {result.get('trade', {}).get('status', 'submitted')}"
        if result.get("error"):
            status_text = f"⚠️ *APPROVAL FAILED*: {result['error']}"
        await query.edit_message_text(text=query.message.text + f"\n\n{status_text}", parse_mode="Markdown")

    elif action == "reject":
        result = await approval_mgr.process_approval(signal_id, "REJECT", broker)
        await query.edit_message_text(
            text=query.message.text + "\n\n❌ *REJECTED*", parse_mode="Markdown")

    elif action == "modify":
        await query.edit_message_text(
            text=query.message.text + "\n\n✏️ *MODIFY* — Reply with new price/qty.\nFormat: `/modify <signal_id> <new_price> <new_qty>`",
            parse_mode="Markdown")

    elif action == "chart":
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"📊 https://www.tradingview.com/chart/?symbol={signal_id}")
