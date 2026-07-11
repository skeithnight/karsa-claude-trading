"""Event subscribers — observers that react to business events.

Shadow mode: these run alongside existing direct calls.
Feature-flagged: only active when event_bus_enabled is true.
"""
from __future__ import annotations
from typing import Dict, Optional
import structlog

from .base import EventEnvelope

logger = structlog.get_logger("event_journal")

# ponytail: lightweight counters, no Prometheus dependency here.
# Prometheus integration lives in src/metrics/crypto_metrics.py.
_counters: Dict[str, int] = {}

# Telegram subscriber state (set via configure_telegram_subscriber)
_telegram_bot = None
_telegram_chat_id: Optional[int] = None


async def metrics_subscriber(event: EventEnvelope) -> None:
    """Count every event type. For Prometheus scrape."""
    _counters[event.event_type] = _counters.get(event.event_type, 0) + 1


async def journal_subscriber(event: EventEnvelope) -> None:
    """Log every event for replay / debugging."""
    logger.info(
        "business_event",
        event_type=event.event_type,
        aggregate_id=event.aggregate_id,
        aggregate_type=event.aggregate_type,
        publisher=event.publisher,
        payload=event.payload,
    )


# Event types worth notifying about on Telegram
_TELEGRAM_EVENTS = {
    "PositionOpened", "PositionReduced", "PositionClosed",
    "TrailingActivated", "BreakEvenActivated", "StopLossTriggered",
    "StopLossRecovered",
}


def _format_event_message(event: EventEnvelope) -> str:
    """Format a business event as a human-readable Telegram message.

    For PositionClosed events, formats as TP/SL alert with PnL.
    """
    from src.utils.format import bold, fmt

    p = event.payload or {}
    emoji = {
        "PositionOpened": "🟢", "PositionReduced": "🟡",
        "PositionClosed": "🔴", "TrailingActivated": "📈",
        "BreakEvenActivated": "🔒", "StopLossTriggered": "⛔",
        "StopLossRecovered": "🛡️",
    }.get(event.event_type, "📌")

    symbol = p.get("symbol") or p.get("ticker", "?")
    side = p.get("side", "")
    reason = p.get("reason", "")

    # P3.2: Format PositionClosed as TP/SL alert
    if event.event_type == "PositionClosed" and reason:
        pnl_usdt = float(p.get("pnl_usdt", 0) or 0)
        pnl_pct = float(p.get("pnl_pct", 0) or 0)
        exit_price = float(p.get("exit_price", 0) or p.get("mark_price", 0) or 0)
        side_label = "LONG" if side == "Buy" else "SHORT"

        is_win = pnl_usdt > 0 or "tp" in reason.lower() or "take_profit" in reason.lower()
        if is_win:
            return fmt(
                bold("🎯 TAKE PROFIT HIT 🎯"), "\n",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
                bold("Symbol: "), f"{symbol} ({side_label})", "\n",
                bold("Exit Price: "), f"${exit_price:,.2f}" if exit_price > 0 else "N/A", "\n",
                bold("PnL: "), f"🟢 ${pnl_usdt:+,.2f} ({pnl_pct:+.2f}%)" if pnl_usdt != 0 else f"🟢 {reason}", "\n\n",
                "Position closed successfully.",
            )
        else:
            return fmt(
                bold("🛑 STOP LOSS HIT 🛑"), "\n",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
                bold("Symbol: "), f"{symbol} ({side_label})", "\n",
                bold("Exit Price: "), f"${exit_price:,.2f}" if exit_price > 0 else "N/A", "\n",
                bold("PnL: "), f"🔴 ${pnl_usdt:+,.2f} ({pnl_pct:+.2f}%)" if pnl_usdt != 0 else f"🔴 {reason}", "\n\n",
                "Position closed to protect capital.",
            )

    # Default format for other events
    msg = f"{emoji} <b>{event.event_type}</b>\n"
    msg += f"  {symbol} {side}"
    if reason:
        msg += f" ({reason})"
    if "pnl_usdt" in p:
        msg += f"\n  PnL: {p['pnl_usdt']} USDT"
    if "new_stop" in p:
        msg += f"\n  New SL: {p['new_stop']}"
    if "r_multiple" in p:
        msg += f"\n  R: {p['r_multiple']:.1f}"
    return msg


async def telegram_subscriber(event: EventEnvelope) -> None:
    """Send key business events to Telegram.

    P3.2: Adds View Dashboard button for TP/SL alerts.
    """
    if not _telegram_bot or not _telegram_chat_id:
        return
    if event.event_type not in _TELEGRAM_EVENTS:
        return
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        text = _format_event_message(event)

        # Add View Dashboard button for PositionClosed events
        reply_markup = None
        if event.event_type == "PositionClosed":
            keyboard = [[InlineKeyboardButton("👀 View Dashboard", callback_data="cmd_dashboard")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

        from src.notifications.router import NotificationRouter, NotificationCategory
        notifier = NotificationRouter(_telegram_bot, _telegram_chat_id)
        cat = NotificationCategory.ASM_REGIME if event.event_type == "RegimeShift" else NotificationCategory.ASM_TRADE
        await notifier.send(text, cat, reply_markup=reply_markup)
    except Exception as e:
        logger.warning("telegram_subscriber_failed", error=str(e))


def configure_telegram_subscriber(bot, chat_id: int) -> None:
    """Wire the Telegram bot for event notifications."""
    global _telegram_bot, _telegram_chat_id
    _telegram_bot = bot
    _telegram_chat_id = chat_id
    logger.info("telegram_subscriber_configured", chat_id=chat_id)


def get_event_counts() -> Dict[str, int]:
    """Return current event counts (for health / metrics endpoint)."""
    return dict(_counters)
