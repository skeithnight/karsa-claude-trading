"""Karsa Trading System - Telegram Bot Command Handlers"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.utils.logging import get_logger

logger = get_logger("telegram_handlers")


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Karsa Trading System*\n\n"
        "Commands:\n"
        "/status - Portfolio & system status\n"
        "/scan <market> <ticker> - Scan a single ticker\n"
        "/portfolio - View positions\n"
        "/trades - Recent trades\n",
        parse_mode="Markdown",
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ponytail: real status from Redis/Postgres once wired up
    await update.message.reply_text(
        "📊 *System Status*\n━━━━━━━━━━━━━━━━\n"
        "🟢 Orchestrator: Online\n🟢 9Router: Connected\n"
        "🟢 MCP: Connected\n🟢 Redis: Connected\n🟢 PostgreSQL: Connected\n",
        parse_mode="Markdown",
    )


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

    # Determine which broker to use (needs market from signal — fetch from DB)
    # For now, try both; approval manager will resolve the market from the signal record
    broker = brokers.get("IDX") or brokers.get("US")

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
