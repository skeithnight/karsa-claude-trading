"""Signal approval flow — Telegram alerts with APPROVE/REJECT buttons."""

from src.utils.format import bold, italic, code, fmt
from src.utils.logging import get_logger

logger = get_logger("approval")


async def send_signal_alert(target, signal_data, signal_id):
    """Send Telegram alert with APPROVE/REJECT buttons for a signal."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from src.utils.validation import sanitize_for_prompt

    conf = signal_data.get("confidence_score", 0)
    ticker = sanitize_for_prompt(signal_data.get("ticker", "?"))
    market = signal_data.get("market", "?")
    direction = signal_data.get("direction", "N/A")
    strategy = signal_data.get("strategy", "Unknown")
    entry = signal_data.get("entry_price")
    target_price = signal_data.get("target_price")
    sl = signal_data.get("stop_loss_price")
    reasoning = (signal_data.get("reasoning", "") or "")[:200]

    d_emoji = "\U0001f7e2" if direction == "LONG" else "\U0001f534" if direction == "SHORT" else "⚪"

    parts = [
        "\U0001f6a8 ", bold(ticker + " (" + market + ")"), "\n",
        d_emoji + " ", bold(direction), " | Conf: " + str(conf) + "/100\n",
        "Strategy: " + strategy + "\n",
    ]

    if entry:
        price_line = str(entry)
        if target_price:
            price_line += " | Target: " + str(target_price)
        if sl:
            price_line += " | SL: " + str(sl)
        parts.append(bold("Entry: ") + price_line + "\n")

    if reasoning:
        parts.extend([bold("Reasoning: "), italic(reasoning), "\n"])

    parts.append("\n⏰ Expires in 24h.")
    text = fmt(*parts)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ APPROVE", callback_data="approve_" + str(signal_id)),
            InlineKeyboardButton("❌ REJECT", callback_data="reject_" + str(signal_id)),
        ]
    ])

    try:
        await target.edit_text(str(text), parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        try:
            await target.reply_text(str(text), parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            pass


async def handle_approval(update, signal_id, action, auth_check):
    """Handle APPROVE or REJECT button callback.

    Args:
        update: Telegram Update object
        signal_id: UUID of the signal
        action: "approve" or "reject"
        auth_check: callable(update) -> bool for authorization
    """
    from src.models.database import async_session
    from src.models.tables import Signal, PaperPosition, PendingApproval
    from sqlalchemy import select
    from datetime import datetime

    if not auth_check(update):
        return

    try:
        async with async_session() as session:
            result = await session.execute(select(Signal).where(Signal.id == signal_id))
            signal = result.scalar_one_or_none()

            if not signal:
                await update.callback_query.answer("Signal not found.", show_alert=True)
                return

            if signal.status != "PENDING":
                await update.callback_query.answer("Already " + str(signal.status), show_alert=True)
                return

            if action == "approve":
                signal.status = "APPROVED"
                direction = signal.direction or "LONG"
                qty = 100 if signal.market == "IDX" else 1

                paper = PaperPosition(
                    signal_id=signal.id,
                    ticker=signal.ticker,
                    market=signal.market,
                    side=direction if direction in ("LONG", "SHORT") else "LONG",
                    quantity=qty,
                    entry_price=float(signal.entry_price) if signal.entry_price else 0,
                    current_price=float(signal.entry_price) if signal.entry_price else 0,
                    target_price=float(signal.target_price) if signal.target_price else None,
                    stop_loss_price=float(signal.stop_loss_price) if signal.stop_loss_price else None,
                    notes="Strategy: " + str(signal.strategy) + ", Conf: " + str(signal.confidence_score),
                )
                session.add(paper)

                pa_result = await session.execute(
                    select(PendingApproval).where(PendingApproval.signal_id == signal_id)
                )
                pa = pa_result.scalar_one_or_none()
                if pa:
                    pa.status = "APPROVED"
                    pa.responded_at = datetime.utcnow()

                await session.commit()
                logger.warning("signal_approved", ticker=signal.ticker, market=signal.market)

                text = fmt(
                    "✅ ", bold("APPROVED: " + signal.ticker), "\n",
                    "Paper position: " + str(qty) + " @ " + str(signal.entry_price or "market") + "\n",
                    "Use ", code("/trades"), " to view."
                )
                await update.callback_query.edit_message_text(str(text), parse_mode="HTML")

            elif action == "reject":
                signal.status = "REJECTED"
                pa_result = await session.execute(
                    select(PendingApproval).where(PendingApproval.signal_id == signal_id)
                )
                pa = pa_result.scalar_one_or_none()
                if pa:
                    pa.status = "REJECTED"
                    pa.responded_at = datetime.utcnow()
                await session.commit()
                logger.info("signal_rejected", ticker=signal.ticker)
                text = fmt("❌ ", bold("REJECTED: " + signal.ticker), "\nSignal discarded.")
                await update.callback_query.edit_message_text(str(text), parse_mode="HTML")

    except Exception as e:
        logger.error("approval_failed", error=str(e), signal_id=str(signal_id), exc_info=True)
        try:
            await update.callback_query.answer("Error processing.", show_alert=True)
        except Exception:
            pass
