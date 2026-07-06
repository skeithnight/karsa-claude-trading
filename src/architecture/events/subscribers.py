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
    """Format a business event as a human-readable Telegram message."""
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
    """Send key business events to Telegram."""
    if not _telegram_bot or not _telegram_chat_id:
        return
    if event.event_type not in _TELEGRAM_EVENTS:
        return
    try:
        text = _format_event_message(event)
        await _telegram_bot.send_message(
            chat_id=_telegram_chat_id, text=text, parse_mode="HTML"
        )
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
